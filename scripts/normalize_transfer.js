// Bank statement transfer payload normalization
const clean = (v) => String(v === undefined || v === null ? '' : v).replace(/^=/, '').trim();

// 0. Transaction Date - handles UTF-8 BOM (\uFEFF) in CSV headers and case-insensitive keys
const rawKeys = Object.keys($json);
const dateKey = rawKeys.find((k) => k.replace(/^\uFEFF/, '').trim().toLowerCase() === 'data transakcji');
const transactionDate = clean(dateKey ? $json[dateKey] : ($json["Data transakcji"] || $json.data_transakcji));

// 1. Invoice Number extracted from transfer title via Regex
const title = clean($json["Tytuł"]);
const invoiceRegex = /((?:K?FV|FZ)\s*[\d\w\/\-]+|\b\d+\/\d+\/\d{4}\b)/i;
const match = title.match(invoiceRegex);
const invoiceNumber = match ? match[0].trim() : title;

// 2. Amount parsing - supports both formats: "1 234,56", "1,234.56", "-1234.56"
let s = clean($json["Kwota transakcji"]).replace(/\s/g, '').replace(/[^\d,.\-]/g, '');
if (s.indexOf(',') > -1 && s.indexOf('.') > -1) {
    s = s.lastIndexOf(',') > s.lastIndexOf('.')
        ? s.replace(/\./g, '').replace(',', '.')
        : s.replace(/,/g, '');
} else if (s.indexOf(',') > -1) {
    s = s.replace(',', '.');
}
const parsed = parseFloat(s);
const numericAmount = isNaN(parsed) ? 0 : Math.abs(parsed);

// 3. Tax ID (NIP) - supports 10-digit formats (1234567890, 123-456-32-18, PL1234567890).
// Regex lookaround prevents extracting 10 digits from inside a 26-digit IBAN account number.
const counterpartySource = clean($json["Dane kontrahenta"]) + ' ' + title;
const nipMatch = counterpartySource.match(/(?<!\d)\d{3}[-\s]?\d{3}[-\s]?\d{2}[-\s]?\d{2}(?!\d)/);
const taxId = nipMatch ? nipMatch[0].replace(/\D/g, '') : '';

// 4. Merge Key - identical string normalization applied on both bank and invoice datasets
const invoiceKey = invoiceNumber.toUpperCase().replace(/\s+/g, '');

return {
    json: {
        ...$json,
        data_transakcji: transactionDate,
        wyczyszczony_nr_faktury: invoiceNumber,
        wyczyszczona_kwota: numericAmount,
        "wyciągnięty_nip": taxId,
        klucz_faktury: invoiceKey
    }
};