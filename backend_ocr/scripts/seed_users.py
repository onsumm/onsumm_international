"""
One-time script: creates users table and seeds all demo users with hashed passwords.
Run from repo root: python3 backend_ocr/scripts/seed_users.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import bcrypt
import psycopg2
from app.config import DB_URL

# (username, password, role, name, email, company_id, department)
USERS = [
    # ── Demo Company 1 ────────────────────────────────────────────────────────
    ('admin',       'admin123',       'admin',              'Arjun Mehta',   'admin@democompany.in',     'd0e00000-0000-0000-0000-000000000001', None),
    ('finance',     'finance123',     'finance_controller', 'Priya Sharma',  'finance@democompany.in',   'd0e00000-0000-0000-0000-000000000001', None),
    ('accountant',  'accountant123',  'accountant',         'Rahul Verma',   'accounts@democompany.in',  'd0e00000-0000-0000-0000-000000000001', None),
    ('approver',    'approver123',    'approver',           'Sneha Kapoor',  'approver@democompany.in',  'd0e00000-0000-0000-0000-000000000001', None),
    ('member',      'member123',      'member',             'Karan Patel',   'member@democompany.in',    'd0e00000-0000-0000-0000-000000000001', None),
    ('auditor',     'auditor123',     'auditor',            'Anjali Nair',   'auditor@democompany.in',   'd0e00000-0000-0000-0000-000000000001', None),
    # ── Demo Company 2 ────────────────────────────────────────────────────────
    ('admin2',      'admin123',       'admin',              'Vikram Singh',  'admin@demo2company.in',    'd0e00000-0000-0000-0000-000000000002', None),
    ('finance2',    'finance123',     'finance_controller', 'Meera Iyer',    'finance@demo2company.in',  'd0e00000-0000-0000-0000-000000000002', None),
    ('accountant2', 'accountant123',  'accountant',         'Suresh Babu',   'accounts@demo2company.in', 'd0e00000-0000-0000-0000-000000000002', None),
    ('approver2',   'approver123',    'approver',           'Divya Menon',   'approver@demo2company.in', 'd0e00000-0000-0000-0000-000000000002', None),
    ('member2',     'member123',      'member',             'Rohan Gupta',   'member@demo2company.in',   'd0e00000-0000-0000-0000-000000000002', None),
    ('auditor2',    'auditor123',     'auditor',            'Lakshmi Nair',  'auditor@demo2company.in',  'd0e00000-0000-0000-0000-000000000002', None),
    # ── Landing plate test (Company 3) — only admin; others added via UI ────────
    ('pooja',    'pooja123',    'admin',    'Pooja Gautam',      'pooja.gautam@brightloans.in',      'd0e00000-0000-0000-0000-000000000003', 'Finance'),
]

COMPANY3_ID = 'd0e00000-0000-0000-0000-000000000003'

# Department chains for company 3: (department, step_order, label, role)
COMPANY3_CHAINS = [
    # Marketing: Shubhra (member) → Mohit (approver) → Salman (admin/finance)
    ('Marketing',             0, 'Submitted',       'member'),
    ('Marketing',             1, 'HOD Approval',    'approver'),
    ('Marketing',             2, 'Finance Approval','admin'),
    # Administration: Shiv (approver) → Salman (admin)
    ('Administration',        0, 'Submitted',       'member'),
    ('Administration',        1, 'HOD Approval',    'approver'),
    ('Administration',        2, 'Finance Approval','admin'),
    # Legal: Arjun (approver) → Salman (admin)
    ('Legal',                 0, 'Submitted',       'member'),
    ('Legal',                 1, 'Manager Approval','approver'),
    ('Legal',                 2, 'Finance Approval','admin'),
    # Information Technology: Ravi (member) → Praveen (approver) → Salman (admin)
    ('Information Technology',0, 'Submitted',       'member'),
    ('Information Technology',1, 'PMO Approval',    'approver'),
    ('Information Technology',2, 'HOD Approval',    'approver'),
    ('Information Technology',3, 'Finance Approval','admin'),
]

conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id            SERIAL PRIMARY KEY,
        username      VARCHAR(50) UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role          VARCHAR(30) NOT NULL,
        name          VARCHAR(100),
        email         VARCHAR(100),
        company_id    UUID NOT NULL,
        department    VARCHAR(100)
    )
""")

for username, password, role, name, email, company_id, department in USERS:
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    cur.execute("""
        INSERT INTO users (username, password_hash, role, name, email, company_id, department)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (username) DO UPDATE
            SET password_hash = EXCLUDED.password_hash,
                role          = EXCLUDED.role,
                name          = EXCLUDED.name,
                email         = EXCLUDED.email,
                company_id    = EXCLUDED.company_id,
                department    = EXCLUDED.department
    """, (username, hashed, role, name, email, company_id, department))
    print(f"  seeded: {username} ({department or 'no dept'})")

conn.commit()  # commit users before chains so user inserts always succeed

# Seed company 3 department chains
for dept, step_order, label, role in COMPANY3_CHAINS:
    cur.execute("""
        INSERT INTO tenant_chain_config (tenant_id, department, step_order, label, role, locked)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (tenant_id, department, step_order) DO UPDATE
            SET label = EXCLUDED.label, role = EXCLUDED.role, locked = EXCLUDED.locked
    """, (COMPANY3_ID, dept, step_order, label, role, step_order == 0))
    print(f"  chain: {dept} step {step_order} → {role}")

conn.commit()
conn.close()
print("Done.")
