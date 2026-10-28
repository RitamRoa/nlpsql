-- =========================================================
-- Domain: Company & Enterprise Operations (Expanded)
-- =========================================================

CREATE TABLE departments (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    manager_id INTEGER,
    budget INTEGER NOT NULL,
    location TEXT NOT NULL
);

CREATE TABLE employees (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    department TEXT NOT NULL,
    salary INTEGER NOT NULL,
    hire_date TEXT NOT NULL,
    job_title TEXT NOT NULL,
    email TEXT NOT NULL,
    performance_rating REAL, -- 1.0 to 5.0
    is_remote INTEGER NOT NULL DEFAULT 0 -- 0=in-office, 1=remote
);

CREATE TABLE sales (
    id INTEGER PRIMARY KEY,
    employee_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    sale_date TEXT NOT NULL,
    product_line TEXT NOT NULL,
    region TEXT NOT NULL,
    FOREIGN KEY (employee_id) REFERENCES employees(id)
);

CREATE TABLE projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    budget INTEGER NOT NULL,
    department_id INTEGER NOT NULL,
    status TEXT NOT NULL, -- 'Active', 'Completed', 'Planning', 'On Hold'
    start_date TEXT NOT NULL,
    end_date TEXT,
    FOREIGN KEY (department_id) REFERENCES departments(id)
);

CREATE TABLE employee_projects (
    employee_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    hours_allocated INTEGER NOT NULL,
    PRIMARY KEY (employee_id, project_id),
    FOREIGN KEY (employee_id) REFERENCES employees(id),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE leaves (
    id INTEGER PRIMARY KEY,
    employee_id INTEGER NOT NULL,
    leave_type TEXT NOT NULL, -- 'Annual', 'Sick', 'Unpaid', 'Maternity', 'Paternity'
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    days_taken INTEGER NOT NULL,
    approved INTEGER NOT NULL DEFAULT 1, -- 1=approved, 0=pending
    FOREIGN KEY (employee_id) REFERENCES employees(id)
);

-- Seed Data: Departments
INSERT INTO departments VALUES (10, 'Sales', 1, 500000, 'New York');
INSERT INTO departments VALUES (20, 'Engineering', 4, 1200000, 'San Francisco');
INSERT INTO departments VALUES (30, 'Marketing', 5, 350000, 'Chicago');
INSERT INTO departments VALUES (40, 'Human Resources', 6, 200000, 'New York');
INSERT INTO departments VALUES (50, 'Finance', 10, 300000, 'Boston');
INSERT INTO departments VALUES (60, 'Operations', 12, 450000, 'Austin');

-- Seed Data: Employees (14 employees)
INSERT INTO employees VALUES (1, 'Alice Smith', 'Sales', 75000, '2023-01-15', 'Sales Director', 'alice@company.com', 4.5, 0);
INSERT INTO employees VALUES (2, 'Bob Jones', 'Engineering', 90000, '2022-06-20', 'Backend Engineer', 'bob@company.com', 4.2, 1);
INSERT INTO employees VALUES (3, 'Charlie Brown', 'Sales', 60000, '2024-03-10', 'Account Executive', 'charlie@company.com', 3.8, 0);
INSERT INTO employees VALUES (4, 'Diana Prince', 'Engineering', 115000, '2021-11-05', 'Principal Architect', 'diana@company.com', 4.9, 1);
INSERT INTO employees VALUES (5, 'Evan Wright', 'Marketing', 65000, '2023-08-25', 'Marketing Lead', 'evan@company.com', 4.0, 0);
INSERT INTO employees VALUES (6, 'Fiona Gallagher', 'Human Resources', 70000, '2022-02-14', 'HR Manager', 'fiona@company.com', 4.3, 0);
INSERT INTO employees VALUES (7, 'George Clark', 'Engineering', 85000, '2023-10-01', 'Frontend Developer', 'george@company.com', 3.9, 1);
INSERT INTO employees VALUES (8, 'Hannah Lee', 'Finance', 80000, '2022-09-12', 'Senior Analyst', 'hannah@company.com', 4.6, 0);
INSERT INTO employees VALUES (9, 'Ivan Petrov', 'Engineering', 95000, '2021-04-20', 'DevOps Engineer', 'ivan@company.com', 4.1, 1);
INSERT INTO employees VALUES (10, 'Julia Chen', 'Finance', 92000, '2020-12-01', 'Finance Manager', 'julia@company.com', 4.7, 0);
INSERT INTO employees VALUES (11, 'Kevin Park', 'Sales', 58000, '2024-05-15', 'Sales Representative', 'kevin@company.com', 3.5, 0);
INSERT INTO employees VALUES (12, 'Laura Martinez', 'Operations', 72000, '2023-03-07', 'Operations Manager', 'laura@company.com', 4.4, 0);
INSERT INTO employees VALUES (13, 'Mike Johnson', 'Engineering', 78000, '2023-07-22', 'QA Engineer', 'mike@company.com', 3.7, 1);
INSERT INTO employees VALUES (14, 'Nina Shah', 'Marketing', 61000, '2024-01-10', 'Content Strategist', 'nina@company.com', 4.1, 0);

-- Seed Data: Sales (20 records with product lines and regions)
INSERT INTO sales VALUES (101, 1,  1500, '2024-01-10', 'Enterprise', 'East');
INSERT INTO sales VALUES (102, 3,   800, '2024-01-15', 'SMB',        'East');
INSERT INTO sales VALUES (103, 1,  2200, '2024-02-05', 'Enterprise', 'West');
INSERT INTO sales VALUES (104, 5,  1200, '2024-02-12', 'SMB',        'Central');
INSERT INTO sales VALUES (105, 3,  1600, '2024-03-01', 'SMB',        'East');
INSERT INTO sales VALUES (106, 1,  3000, '2024-03-15', 'Enterprise', 'East');
INSERT INTO sales VALUES (107, 2,   0,   '2024-03-20', 'Internal',   'West');
INSERT INTO sales VALUES (108, 11, 950,  '2024-04-05', 'SMB',        'Central');
INSERT INTO sales VALUES (109, 1,  4100, '2024-04-10', 'Enterprise', 'East');
INSERT INTO sales VALUES (110, 3,  2300, '2024-04-18', 'Enterprise', 'West');
INSERT INTO sales VALUES (111, 11, 1100, '2024-05-02', 'SMB',        'East');
INSERT INTO sales VALUES (112, 1,  1800, '2024-05-20', 'Enterprise', 'Central');
INSERT INTO sales VALUES (113, 3,  900,  '2024-06-01', 'SMB',        'East');
INSERT INTO sales VALUES (114, 11, 1400, '2024-06-15', 'SMB',        'West');
INSERT INTO sales VALUES (115, 1,  2700, '2024-07-04', 'Enterprise', 'East');
INSERT INTO sales VALUES (116, 3,  1950, '2024-07-20', 'Enterprise', 'Central');
INSERT INTO sales VALUES (117, 11, 2100, '2024-08-10', 'SMB',        'East');
INSERT INTO sales VALUES (118, 1,  3300, '2024-08-25', 'Enterprise', 'West');
INSERT INTO sales VALUES (119, 3,  1750, '2024-09-12', 'SMB',        'East');
INSERT INTO sales VALUES (120, 11, 2600, '2024-09-30', 'Enterprise', 'Central');

-- Seed Data: Projects
INSERT INTO projects VALUES (501, 'Cloud Migration',         250000, 20, 'Active',    '2024-01-01', NULL);
INSERT INTO projects VALUES (502, 'CRM Modernization',       120000, 10, 'Active',    '2024-02-15', NULL);
INSERT INTO projects VALUES (503, 'Global Rebrand',           80000, 30, 'Completed', '2023-09-01', '2024-01-31');
INSERT INTO projects VALUES (504, 'Zero Trust Security',     180000, 20, 'Planning',  '2024-06-01', NULL);
INSERT INTO projects VALUES (505, 'HR Analytics Dashboard',   45000, 40, 'Active',    '2024-03-01', NULL);
INSERT INTO projects VALUES (506, 'Financial Reporting Automation', 90000, 50, 'Completed', '2023-11-01', '2024-04-30');
INSERT INTO projects VALUES (507, 'Ops Efficiency Drive',    130000, 60, 'Active',    '2024-04-01', NULL);

-- Seed Data: Employee Projects
INSERT INTO employee_projects VALUES (2,  501, 'Lead Backend Developer', 35);
INSERT INTO employee_projects VALUES (4,  501, 'Solution Architect',     20);
INSERT INTO employee_projects VALUES (7,  501, 'UI Integrator',          30);
INSERT INTO employee_projects VALUES (9,  501, 'DevOps Lead',            40);
INSERT INTO employee_projects VALUES (13, 501, 'QA Lead',                25);
INSERT INTO employee_projects VALUES (1,  502, 'Executive Sponsor',      10);
INSERT INTO employee_projects VALUES (3,  502, 'Functional Analyst',     25);
INSERT INTO employee_projects VALUES (11, 502, 'Sales Liaison',          15);
INSERT INTO employee_projects VALUES (5,  503, 'Campaign Manager',       40);
INSERT INTO employee_projects VALUES (14, 503, 'Content Producer',       35);
INSERT INTO employee_projects VALUES (6,  505, 'Project Owner',          20);
INSERT INTO employee_projects VALUES (8,  506, 'Senior Analyst',         30);
INSERT INTO employee_projects VALUES (10, 506, 'Finance Lead',           25);
INSERT INTO employee_projects VALUES (12, 507, 'Operations Lead',        40);

-- Seed Data: Leaves
INSERT INTO leaves VALUES (1,  1,  'Annual',    '2024-07-01', '2024-07-05', 5, 1);
INSERT INTO leaves VALUES (2,  2,  'Sick',      '2024-03-10', '2024-03-12', 3, 1);
INSERT INTO leaves VALUES (3,  4,  'Maternity', '2024-04-01', '2024-07-31', 90, 1);
INSERT INTO leaves VALUES (4,  7,  'Annual',    '2024-08-15', '2024-08-19', 5, 1);
INSERT INTO leaves VALUES (5,  9,  'Sick',      '2024-02-05', '2024-02-06', 2, 1);
INSERT INTO leaves VALUES (6,  11, 'Annual',    '2024-09-02', '2024-09-06', 5, 1);
INSERT INTO leaves VALUES (7,  12, 'Paternity', '2024-05-20', '2024-06-03', 15, 1);
INSERT INTO leaves VALUES (8,  13, 'Annual',    '2024-10-07', '2024-10-11', 5, 1);
INSERT INTO leaves VALUES (9,  5,  'Sick',      '2024-06-17', '2024-06-18', 2, 1);
INSERT INTO leaves VALUES (10, 6,  'Annual',    '2024-12-23', '2024-12-27', 5, 0);
