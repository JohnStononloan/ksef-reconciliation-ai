import os
import time
import requests
import xmltodict
from pathlib import Path
from dotenv import load_dotenv
from ksef_client import KsefClient, KsefClientOptions, KsefEnvironment
from ksef_client import models as m
from ksef_client.services import AuthCoordinator

load_dotenv()

# ==============================================================================
# 1. CONFIGURATION PARAMETERS (ENV VARIABLES)
# ==============================================================================
KSEF_TOKEN = os.getenv("KSEF_TOKEN", "")
CONTEXT_TYPE = os.getenv("KSEF_CONTEXT_TYPE", "nip")
CONTEXT_VALUE = os.getenv("KSEF_CONTEXT_VALUE", "")

N8N_WEBHOOK_URL = os.getenv(
    "N8N_WEBHOOK_URL", "https://n8n.example.com/webhook/ksef-ingest")
N8N_API_KEY = os.getenv("N8N_API_KEY", "")

MAX_INVOICES = int(os.getenv("MAX_INVOICES", "10"))
KSEF_ENV = os.getenv("KSEF_ENV", "TEST").upper()

if not KSEF_TOKEN or not CONTEXT_VALUE:
    raise RuntimeError(
        "Missing KSEF_TOKEN or KSEF_CONTEXT_VALUE environment variables!")

print("🚀 Initializing KSeF -> n8n Ingestion Pipeline...")

# ==============================================================================
# 2. CLIENT INITIALIZATION & SESSION AUTHORIZATION
# ==============================================================================
environment = KsefEnvironment.PROD if KSEF_ENV == "PROD" else KsefEnvironment.TEST
options = KsefClientOptions(base_url=environment.value)

with KsefClient(options) as client:
    print("🔒 Fetching public key certificate from MF servers...")
    token_cert_pem = client.security.get_public_key_certificate_pem(
        m.PublicKeyCertificateUsage.KSEFTOKENENCRYPTION,
    )

    print(f"🔑 Authorizing session in {KSEF_ENV} environment...")
    access_token = AuthCoordinator(client.auth).authenticate_with_ksef_token(
        token=KSEF_TOKEN,
        public_certificate=token_cert_pem,
        context_identifier_type=CONTEXT_TYPE,
        context_identifier_value=CONTEXT_VALUE,
    ).access_token

    print("✅ Session authorized. Querying invoices...")

    # ==============================================================================
    # 3. QUERY INVOICE METADATA
    # ==============================================================================
    date_from = os.getenv("QUERY_DATE_FROM", "2026-07-01T00:00:00Z")
    date_to = os.getenv("QUERY_DATE_TO", "2026-07-13T23:59:59Z")

    metadata = client.invoices.query_invoice_metadata_by_date_range(
        subject_type=m.InvoiceQuerySubjectType.SUBJECT1,
        date_type=m.InvoiceQueryDateType.ISSUE,
        date_from=date_from,
        date_to=date_to,
        access_token=access_token,
        page_size=MAX_INVOICES,
    )

    fetched_invoices = metadata.invoices[:MAX_INVOICES]
    total_invoices = len(fetched_invoices)

    print(
        f"📊 Identified invoices to process (max limit {MAX_INVOICES}): {total_invoices}")

    if total_invoices == 0:
        print("ℹ️ No invoices found for specified criteria.")
    else:
        print("📡 Starting HTTP streaming to n8n webhook...")

        available_methods = dir(client.invoices)
        download_method_name = next(
            (n for n in ['get_invoice', 'get_invoice_xml', 'download_invoice',
                         'get_invoice_payload'] if n in available_methods),
            None
        )

        if not download_method_name:
            print("❌ CRITICAL: Incompatible download method in client library.")
            exit(1)

        download_method = getattr(client.invoices, download_method_name)
        base_delay = 2.0

        # ==============================================================================
        # 4. ITERATIVE EXTRACTION & N8N TRANSMISSION
        # ==============================================================================
        for idx, inv_meta in enumerate(fetched_invoices, start=1):
            ksef_number = (
                getattr(inv_meta, 'ksef_number', None) or
                getattr(inv_meta, 'ksef_reference_number', None) or
                getattr(inv_meta, 'reference_number', None)
            )

            if not ksef_number:
                print(
                    f"   ⚠️ [{idx}/{total_invoices}] Skipped - identifier missing.")
                continue

            print(f"[{idx}/{total_invoices}] Processing invoice: {ksef_number}...")

            max_retries = 3
            retry_count = 0
            backoff_delay = 5.0
            success = False

            while retry_count < max_retries and not success:
                try:
                    # 1. Download raw XML payload
                    try:
                        invoice_data = download_method(
                            ksef_reference_number=ksef_number, access_token=access_token)
                    except TypeError:
                        invoice_data = download_method(
                            ksef_number, access_token=access_token)

                    xml_content = invoice_data
                    if hasattr(invoice_data, 'content') and invoice_data.content:
                        xml_content = invoice_data.content
                    elif hasattr(invoice_data, 'payload') and invoice_data.payload:
                        xml_content = invoice_data.payload

                    if isinstance(xml_content, bytes):
                        xml_content = xml_content.decode('utf-8')

                    # 2. In-memory XML to dict conversion
                    parsed_dict = xmltodict.parse(xml_content)

                    payload_for_n8n = {
                        "ksef_number": ksef_number,
                        "faktura_data": parsed_dict
                    }

                    # 3. HTTP POST to n8n webhook with header authorization
                    response = requests.post(
                        N8N_WEBHOOK_URL,
                        json=payload_for_n8n,
                        headers={
                            "Content-Type": "application/json",
                            "x-api-key": N8N_API_KEY
                        },
                        timeout=10
                    )

                    if response.status_code in [200, 201]:
                        try:
                            resp_json = response.json()
                            exec_id = resp_json.get("execution_id", "no_id")
                            print(
                                f"   -> 🚀 Invoice {ksef_number} dispatched! (n8n Execution ID: {exec_id})")
                        except ValueError:
                            print(
                                f"   -> 🚀 Invoice {ksef_number} dispatched (non-JSON response received).")

                        success = True
                    elif response.status_code == 401:
                        print("   ❌ AUTHORIZATION ERROR (401). Verify N8N_API_KEY.")
                        break
                    else:
                        print(
                            f"   ❌ Webhook rejected request. Status code: {response.status_code}")
                        break

                    time.sleep(base_delay)

                except Exception as e:
                    error_msg = str(e)
                    if "429" in error_msg or "Too Many Requests" in error_msg:
                        retry_count += 1
                        if retry_count < max_retries:
                            print(
                                f"   ⚠️ [API Rate Limit] HTTP 429. Retry {retry_count}/{max_retries}. Backoff: {backoff_delay}s...")
                            time.sleep(backoff_delay)
                            backoff_delay *= 2.0
                        else:
                            print(
                                f"   ❌ [FAILED] Skipped {ksef_number}. Upstream rate limit exceeded.")
                    else:
                        print(
                            f"   ❌ Critical error on invoice {ksef_number}: {error_msg}")
                        time.sleep(base_delay)
                        break

    print("\n🏁 Invoice batch ingestion completed successfully!")
