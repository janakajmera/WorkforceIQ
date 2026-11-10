-- =====================================================================================
-- WorkforceIQ | 04 | DML -- conformed reference (master) data for the OLTP lookups
-- These are the canonical values that the staging ETL maps free-text values onto.
-- Idempotent (INSERT IGNORE on unique names).
-- =====================================================================================
INSERT IGNORE INTO wfiq_oltp.departments (department_name, department_code) VALUES
    ('Human Resources',        'HR'),
    ('Research & Development', 'RND'),
    ('Sales',                  'SAL');

INSERT IGNORE INTO wfiq_oltp.job_roles (role_title) VALUES
    ('Healthcare Representative'), ('Human Resources'), ('Laboratory Technician'),
    ('Manager'), ('Manufacturing Director'), ('Research Director'),
    ('Research Scientist'), ('Sales Executive'), ('Sales Representative');

INSERT IGNORE INTO wfiq_oltp.education_fields (field_name) VALUES
    ('Human Resources'), ('Life Sciences'), ('Marketing'), ('Medical'), ('Technical Degree'), ('Other');
