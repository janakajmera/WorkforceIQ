-- =====================================================================================
-- WorkforceIQ | 07 | OLTP transactional procedures used by the application
-- Each procedure validates input (SIGNAL -> friendly message in the UI) and keeps employees and
-- employee_history in step, atomically. The DW is synchronised afterwards (see 06_*.sql).
-- =====================================================================================
DELIMITER //

DROP PROCEDURE IF EXISTS wfiq_oltp.sp_oltp_onboard_employee //
CREATE PROCEDURE wfiq_oltp.sp_oltp_onboard_employee(
    IN p_first_name VARCHAR(80), IN p_last_name VARCHAR(80), IN p_email VARCHAR(160),
    IN p_gender VARCHAR(10), IN p_date_of_birth DATE, IN p_marital_status VARCHAR(10),
    IN p_education_level TINYINT, IN p_education_field_id SMALLINT, IN p_department_id SMALLINT,
    IN p_job_role_id SMALLINT, IN p_job_level TINYINT, IN p_monthly_income INT, IN p_hire_date DATE,
    IN p_business_travel VARCHAR(20), IN p_over_time TINYINT, IN p_distance_from_home SMALLINT,
    IN p_total_working_years TINYINT, IN p_num_companies_worked TINYINT)
BEGIN
    DECLARE v_number INT UNSIGNED;
    DECLARE v_id INT UNSIGNED;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    IF p_hire_date > DATE_ADD(CURRENT_DATE, INTERVAL 366 DAY) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Hire date cannot be more than a year in the future.';
    END IF;
    IF p_date_of_birth > DATE_SUB(p_hire_date, INTERVAL 16 YEAR) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Employee must be at least 16 years old on the hire date.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM wfiq_oltp.departments WHERE department_id = p_department_id) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown department.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM wfiq_oltp.job_roles WHERE job_role_id = p_job_role_id) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown job role.';
    END IF;
    IF EXISTS (SELECT 1 FROM wfiq_oltp.employees WHERE email = LOWER(TRIM(p_email))) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'An employee with this email already exists.';
    END IF;

    START TRANSACTION;
    SELECT COALESCE(MAX(employee_number), 0) + 1 INTO v_number FROM wfiq_oltp.employees FOR UPDATE;
    INSERT INTO wfiq_oltp.employees
        (employee_number, first_name, last_name, email, gender, date_of_birth, marital_status, education_level,
         education_field_id, department_id, job_role_id, job_level, monthly_income, hire_date, business_travel,
         over_time, distance_from_home, total_working_years, num_companies_worked)
    VALUES (v_number, TRIM(p_first_name), TRIM(p_last_name), LOWER(TRIM(p_email)), p_gender, p_date_of_birth,
            p_marital_status, p_education_level, p_education_field_id, p_department_id, p_job_role_id,
            p_job_level, p_monthly_income, p_hire_date, p_business_travel, p_over_time, p_distance_from_home,
            p_total_working_years, p_num_companies_worked);
    SET v_id = LAST_INSERT_ID();
    INSERT INTO wfiq_oltp.employee_history
        (employee_id, effective_date, department_id, job_role_id, job_level, monthly_income, change_type)
    VALUES (v_id, p_hire_date, p_department_id, p_job_role_id, p_job_level, p_monthly_income, 'Hire');
    COMMIT;
    SELECT v_id AS employee_id, v_number AS employee_number;
END //

-- Department transfer / promotion / raise: updates the employee and appends a history version atomically.
DROP PROCEDURE IF EXISTS wfiq_oltp.sp_oltp_change_employee_job //
CREATE PROCEDURE wfiq_oltp.sp_oltp_change_employee_job(
    IN p_employee_id INT UNSIGNED, IN p_department_id SMALLINT, IN p_job_role_id SMALLINT,
    IN p_job_level TINYINT, IN p_monthly_income INT, IN p_effective_date DATE, IN p_change_type VARCHAR(20))
BEGIN
    DECLARE v_dept SMALLINT; DECLARE v_role SMALLINT; DECLARE v_level TINYINT; DECLARE v_income INT;
    DECLARE v_term DATE; DECLARE v_last DATE; DECLARE v_type VARCHAR(20);
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    SELECT department_id, job_role_id, job_level, monthly_income, termination_date
      INTO v_dept, v_role, v_level, v_income, v_term
    FROM wfiq_oltp.employees WHERE employee_id = p_employee_id;
    IF v_dept IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Employee not found.';
    END IF;
    IF v_term IS NOT NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Cannot change a former employee.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM wfiq_oltp.departments WHERE department_id = p_department_id) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown department.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM wfiq_oltp.job_roles WHERE job_role_id = p_job_role_id) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown job role.';
    END IF;
    IF p_department_id = v_dept AND p_job_role_id = v_role AND p_job_level = v_level AND p_monthly_income = v_income THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Nothing to change: the new values equal the current values.';
    END IF;
    SELECT MAX(effective_date) INTO v_last FROM wfiq_oltp.employee_history WHERE employee_id = p_employee_id;
    IF p_effective_date < v_last THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Effective date is earlier than the most recent recorded change for this employee.';
    END IF;
    IF p_effective_date > DATE_ADD(CURRENT_DATE, INTERVAL 366 DAY) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Effective date cannot be more than a year in the future.';
    END IF;

    SET v_type = COALESCE(NULLIF(p_change_type, ''),
        CASE WHEN p_department_id <> v_dept THEN 'Transfer'
             WHEN p_job_level > v_level THEN 'Promotion'
             WHEN p_job_role_id <> v_role OR p_job_level <> v_level THEN 'Role Change'
             ELSE 'Salary Increase' END);

    START TRANSACTION;
    UPDATE wfiq_oltp.employees
       SET department_id = p_department_id, job_role_id = p_job_role_id,
           job_level = p_job_level, monthly_income = p_monthly_income
     WHERE employee_id = p_employee_id;
    INSERT INTO wfiq_oltp.employee_history
        (employee_id, effective_date, department_id, job_role_id, job_level, monthly_income, change_type)
    VALUES (p_employee_id, p_effective_date, p_department_id, p_job_role_id, p_job_level, p_monthly_income, v_type)
    AS new_row
    ON DUPLICATE KEY UPDATE department_id = new_row.department_id, job_role_id = new_row.job_role_id,
                            job_level = new_row.job_level, monthly_income = new_row.monthly_income,
                            change_type = new_row.change_type;      -- same-day correction replaces the version
    COMMIT;
    SELECT v_type AS change_type;
END //

DROP PROCEDURE IF EXISTS wfiq_oltp.sp_oltp_terminate_employee //
CREATE PROCEDURE wfiq_oltp.sp_oltp_terminate_employee(IN p_employee_id INT UNSIGNED, IN p_termination_date DATE)
BEGIN
    DECLARE v_hire DATE; DECLARE v_term DATE;
    SELECT hire_date, termination_date INTO v_hire, v_term FROM wfiq_oltp.employees WHERE employee_id = p_employee_id;
    IF v_hire IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Employee not found.';
    END IF;
    IF v_term IS NOT NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Employee has already left the company.';
    END IF;
    IF p_termination_date < v_hire THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Termination date cannot be before the hire date.';
    END IF;
    IF p_termination_date > DATE_ADD(CURRENT_DATE, INTERVAL 366 DAY) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Termination date cannot be more than a year in the future.';
    END IF;
    UPDATE wfiq_oltp.employees SET termination_date = p_termination_date WHERE employee_id = p_employee_id;
    -- close any open project assignments on the day the employee leaves
    UPDATE wfiq_oltp.assignments SET end_date = GREATEST(start_date, p_termination_date)
     WHERE employee_id = p_employee_id AND (end_date IS NULL OR end_date > p_termination_date);
END //

DELIMITER ;
