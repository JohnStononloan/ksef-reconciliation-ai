-- KSeF Invoice Database Schema Initialization
CREATE TABLE IF NOT EXISTS invoices (
    id SERIAL PRIMARY KEY,
    ksef_number VARCHAR(100) UNIQUE,
    invoice_number VARCHAR(100) NOT NULL,
    issue_date DATE NOT NULL,
    seller_nip VARCHAR(20) NOT NULL,
    seller_name TEXT,
    buyer_nip VARCHAR(20) NOT NULL,
    buyer_name TEXT,
    net_amount NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    vat_amount NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    gross_amount NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    currency VARCHAR(10) DEFAULT 'PLN',
    payment_due_date VARCHAR(50),
    bank_account TEXT,
    payment_status VARCHAR(50) DEFAULT 'UNPAID',
    paid_at TIMESTAMP WITH TIME ZONE,
    paid_amount NUMERIC(12, 2),
    execution_id TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Optimization indexes for Slack bot queries and MCP server lookup
CREATE INDEX IF NOT EXISTS idx_invoices_invoice_number ON invoices(invoice_number);
CREATE INDEX IF NOT EXISTS idx_invoices_seller_nip ON invoices(seller_nip);
CREATE INDEX IF NOT EXISTS idx_invoices_buyer_nip ON invoices(buyer_nip);
CREATE INDEX IF NOT EXISTS idx_invoices_payment_status ON invoices(payment_status);