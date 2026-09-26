import os
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Depends, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

MCP_SECRET = os.getenv("MCP_SECRET")
if not MCP_SECRET:
    raise RuntimeError("Missing MCP_SECRET environment variable!")

app = FastAPI(title="Invoice Ingestion & Query API")
security = HTTPBearer()


def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    if credentials.credentials != MCP_SECRET:
        raise HTTPException(
            status_code=401, detail="Unauthorized: Invalid Bearer Token")


def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "postgres"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "ksef_db"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASS", "postgres"),
        cursor_factory=RealDictCursor
    )


class InvoicePayload(BaseModel):
    ksef_number: str = Field(..., max_length=100)
    nr_faktury: str = Field(..., max_length=100)
    data_wystawienia: str
    nip_sprzedawcy: str = Field(..., max_length=20)
    nazwa_sprzedawcy: Optional[str] = ""
    nip_nabywcy: Optional[str] = ""
    nazwa_nabywcy: Optional[str] = ""
    kwota_netto: float = 0.0
    kwota_vat: float = 0.0
    kwota_brutto: float = 0.0
    waluta: Optional[str] = "PLN"
    termin_platnosci: Optional[str] = None
    konto_bankowe: Optional[str] = "BRAK"
    status_platnosci: Optional[str] = "NIEOPŁACONA"


class UpdateStatusPayload(BaseModel):
    invoice_number: str = Field(..., max_length=100)
    status: str = Field(default="paid")

# --- 1. ENDPOINT FOR N8N (INGEST INVOICES INTO DATABASE) ---


@app.post("/add-invoice", dependencies=[Depends(verify_token)])
def add_invoice(payload: InvoicePayload):
    conn = get_db_connection()
    cur = conn.cursor()

    db_status = "paid" if str(payload.status_platnosci).upper() in [
        "OPŁACONA", "OPŁACONO"] else "unpaid"
    clean_due_date = payload.termin_platnosci if payload.termin_platnosci not in [
        "OPŁACONO", "", None] else None

    query = """
        INSERT INTO invoices (
            ksef_number, invoice_number, issue_date,
            issuer_nip, issuer_name, buyer_nip, buyer_name,
            net_amount, vat_amount, gross_amount, currency,
            due_date, bank_account, status
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (ksef_number) DO UPDATE SET
            status = EXCLUDED.status,
            bank_account = EXCLUDED.bank_account,
            due_date = EXCLUDED.due_date;
    """

    try:
        cur.execute(query, (
            payload.ksef_number, payload.nr_faktury, payload.data_wystawienia,
            payload.nip_sprzedawcy, payload.nazwa_sprzedawcy, payload.nip_nabywcy, payload.nazwa_nabywcy,
            payload.kwota_netto, payload.kwota_vat, payload.kwota_brutto, payload.waluta,
            clean_due_date, payload.konto_bankowe, db_status
        ))
        conn.commit()
        return {"success": True, "ksef_number": payload.ksef_number, "message": "Invoice saved successfully."}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

# --- 2. ENDPOINT FOR N8N (UPDATE PAYMENT STATUS) ---


@app.post("/update-status", dependencies=[Depends(verify_token)])
def update_invoice_status(payload: UpdateStatusPayload):
    conn = get_db_connection()
    cur = conn.cursor()

    db_status = "paid" if payload.status.lower(
    ) in ["paid", "opłacono", "opłacona"] else payload.status.lower()

    query = """
        UPDATE invoices
        SET status = %s,
            paid_at = NOW()
        WHERE invoice_number = %s;
    """

    try:
        cur.execute(query, (db_status, payload.invoice_number))
        if cur.rowcount == 0:
            conn.rollback()
            raise HTTPException(
                status_code=404, detail=f"Invoice number '{payload.invoice_number}' not found.")

        conn.commit()
        return {
            "success": True,
            "invoice_number": payload.invoice_number,
            "status": db_status,
            "message": "Invoice status updated successfully."
        }
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

# --- 3. ENDPOINT FOR SLACK BOT / MCP TOOL (READ INVOICES FOR AI AGENT) ---


@app.get("/invoices", dependencies=[Depends(verify_token)])
def get_invoices(limit: int = 50, status: Optional[str] = None):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if status:
            cur.execute(
                "SELECT * FROM invoices WHERE status = %s ORDER BY issue_date DESC LIMIT %s;", (status, limit))
        else:
            cur.execute(
                "SELECT * FROM invoices ORDER BY issue_date DESC LIMIT %s;", (limit,))

        rows = cur.fetchall()
        return {"success": True, "data": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
