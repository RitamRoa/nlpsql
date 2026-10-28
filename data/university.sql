-- =========================================================
-- Domain: University & Course Management (Expanded)
-- =========================================================

CREATE TABLE departments (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    building TEXT NOT NULL,
    head_instructor_id INTEGER -- references instructors(id)
);

CREATE TABLE instructors (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    department_id INTEGER NOT NULL,
    title TEXT NOT NULL, -- 'Professor', 'Associate Professor', 'Lecturer', 'Adjunct'
    email TEXT NOT NULL,
    office TEXT NOT NULL,
    FOREIGN KEY (department_id) REFERENCES departments(id)
);

CREATE TABLE courses (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL,
    title TEXT NOT NULL,
    credits INTEGER NOT NULL,
    department_id INTEGER NOT NULL,
    instructor_id INTEGER NOT NULL,
    max_enrollment INTEGER NOT NULL DEFAULT 30,
    FOREIGN KEY (department_id) REFERENCES departments(id),
    FOREIGN KEY (instructor_id) REFERENCES instructors(id)
);

CREATE TABLE students (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    major TEXT NOT NULL,
    year INTEGER NOT NULL, -- 1=Freshman, 2=Sophomore, 3=Junior, 4=Senior
    gpa REAL,
    enrollment_date TEXT NOT NULL
);

CREATE TABLE enrollments (
    id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    grade TEXT NOT NULL, -- 'A', 'A-', 'B+', 'B', 'B-', 'C+', 'C', 'D', 'F', 'W'
    semester TEXT NOT NULL,
    attendance_pct REAL, -- 0-100
    FOREIGN KEY (course_id) REFERENCES courses(id),
    FOREIGN KEY (student_id) REFERENCES students(id)
);

CREATE TABLE scholarships (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    amount REAL NOT NULL,
    awarded_year INTEGER NOT NULL,
    FOREIGN KEY (student_id) REFERENCES students(id)
);

-- Seed Data: Departments (with instructors added after)
INSERT INTO departments VALUES (1, 'Computer Science', 'Turing Hall',   101);
INSERT INTO departments VALUES (2, 'Mathematics',      'Euler Building', 103);
INSERT INTO departments VALUES (3, 'Physics',          'Feynman Complex',105);
INSERT INTO departments VALUES (4, 'Business',         'Adams Hall',     106);

-- Seed Data: Instructors
INSERT INTO instructors VALUES (101, 'Dr. Alan Turing',    1, 'Professor',           'turing@uni.edu',   'TH-201');
INSERT INTO instructors VALUES (102, 'Dr. Ada Lovelace',   1, 'Associate Professor', 'lovelace@uni.edu', 'TH-205');
INSERT INTO instructors VALUES (103, 'Dr. Carl Gauss',     2, 'Professor',           'gauss@uni.edu',    'EB-301');
INSERT INTO instructors VALUES (104, 'Dr. Emmy Noether',   2, 'Associate Professor', 'noether@uni.edu',  'EB-310');
INSERT INTO instructors VALUES (105, 'Dr. Richard Feynman',3, 'Professor',           'feynman@uni.edu',  'FC-110');
INSERT INTO instructors VALUES (106, 'Dr. Peter Drucker',  4, 'Professor',           'drucker@uni.edu',  'AH-402');
INSERT INTO instructors VALUES (107, 'Ms. Grace Hopper',   1, 'Lecturer',            'hopper@uni.edu',   'TH-210');
INSERT INTO instructors VALUES (108, 'Mr. John Nash',      2, 'Adjunct',             'nash@uni.edu',     'EB-315');

-- Seed Data: Courses (9 courses, each with instructor)
INSERT INTO courses VALUES (201, 'CS101',    'Intro to Computer Science',    4, 1, 101, 35);
INSERT INTO courses VALUES (202, 'CS205',    'Data Structures & Algorithms', 4, 1, 102, 30);
INSERT INTO courses VALUES (203, 'CS340',    'Database Systems',             3, 1, 107, 25);
INSERT INTO courses VALUES (204, 'MATH301',  'Linear Algebra',               3, 2, 103, 30);
INSERT INTO courses VALUES (205, 'MATH320',  'Abstract Algebra',             3, 2, 104, 25);
INSERT INTO courses VALUES (206, 'MATH210',  'Calculus II',                  4, 2, 108, 40);
INSERT INTO courses VALUES (207, 'PHYS150',  'Quantum Mechanics I',          4, 3, 105, 20);
INSERT INTO courses VALUES (208, 'BUS101',   'Business Fundamentals',        3, 4, 106, 50);
INSERT INTO courses VALUES (209, 'BUS220',   'Financial Accounting',         3, 4, 106, 40);

-- Seed Data: Students (10 students)
INSERT INTO students VALUES (1,  'Maya Lin',       'maya@student.edu',     'Computer Science', 3, 3.95, '2022-09-01');
INSERT INTO students VALUES (2,  'Lucas Miller',   'lucas@student.edu',    'Mathematics',      3, 3.70, '2022-09-01');
INSERT INTO students VALUES (3,  'Chloe Bennett',  'chloe@student.edu',    'Physics',          3, 3.85, '2022-09-01');
INSERT INTO students VALUES (4,  'Ethan Hunt',     'ethan@student.edu',    'Computer Science', 2, 3.10, '2023-09-01');
INSERT INTO students VALUES (5,  'Priya Kapoor',   'priya@student.edu',    'Business',         4, 3.75, '2021-09-01');
INSERT INTO students VALUES (6,  'Sam Torres',     'sam@student.edu',      'Mathematics',      2, 3.40, '2023-09-01');
INSERT INTO students VALUES (7,  'Zoe Williams',   'zoe@student.edu',      'Computer Science', 1, 3.60, '2024-09-01');
INSERT INTO students VALUES (8,  'Alex Kim',       'alex@student.edu',     'Business',         4, 3.90, '2021-09-01');
INSERT INTO students VALUES (9,  'Jordan Patel',   'jordan@student.edu',   'Physics',          2, 3.20, '2023-09-01');
INSERT INTO students VALUES (10, 'Mia Robinson',   'mia@student.edu',      'Computer Science', 1, 3.80, '2024-09-01');

-- Seed Data: Enrollments (using student_id)
INSERT INTO enrollments VALUES (1,  201, 1,  'A',  'Fall 2022',   97.0);
INSERT INTO enrollments VALUES (2,  202, 1,  'A',  'Spring 2023', 95.0);
INSERT INTO enrollments VALUES (3,  203, 1,  'A-', 'Fall 2023',   92.0);
INSERT INTO enrollments VALUES (4,  201, 2,  'B',  'Fall 2022',   88.0);
INSERT INTO enrollments VALUES (5,  204, 2,  'A',  'Spring 2023', 90.0);
INSERT INTO enrollments VALUES (6,  205, 2,  'B+', 'Fall 2023',   85.0);
INSERT INTO enrollments VALUES (7,  207, 3,  'A',  'Fall 2022',   98.0);
INSERT INTO enrollments VALUES (8,  205, 3,  'B',  'Spring 2023', 80.0);
INSERT INTO enrollments VALUES (9,  201, 4,  'C',  'Fall 2023',   70.0);
INSERT INTO enrollments VALUES (10, 202, 4,  'C+', 'Spring 2024', 75.0);
INSERT INTO enrollments VALUES (11, 208, 5,  'A-', 'Fall 2021',   93.0);
INSERT INTO enrollments VALUES (12, 209, 5,  'A',  'Spring 2022', 96.0);
INSERT INTO enrollments VALUES (13, 208, 8,  'A',  'Fall 2021',   99.0);
INSERT INTO enrollments VALUES (14, 209, 8,  'A',  'Spring 2022', 97.0);
INSERT INTO enrollments VALUES (15, 206, 6,  'B-', 'Spring 2024', 78.0);
INSERT INTO enrollments VALUES (16, 204, 6,  'B',  'Fall 2023',   82.0);
INSERT INTO enrollments VALUES (17, 201, 7,  'A-', 'Fall 2024',   91.0);
INSERT INTO enrollments VALUES (18, 203, 7,  'B+', 'Fall 2024',   88.0);
INSERT INTO enrollments VALUES (19, 207, 9,  'C+', 'Fall 2023',   72.0);
INSERT INTO enrollments VALUES (20, 206, 9,  'B',  'Spring 2024', 83.0);
INSERT INTO enrollments VALUES (21, 201, 10, 'A',  'Fall 2024',   99.0);
INSERT INTO enrollments VALUES (22, 208, 10, 'A-', 'Fall 2024',   94.0);

-- Seed Data: Scholarships
INSERT INTO scholarships VALUES (1, 1,  'Dean''s Excellence Award',    5000, 2023);
INSERT INTO scholarships VALUES (2, 3,  'STEM Achievement Scholarship', 3000, 2023);
INSERT INTO scholarships VALUES (3, 8,  'Business Leadership Grant',    4000, 2022);
INSERT INTO scholarships VALUES (4, 5,  'Business Leadership Grant',    4000, 2022);
INSERT INTO scholarships VALUES (5, 1,  'Dean''s Excellence Award',    5000, 2024);
INSERT INTO scholarships VALUES (6, 10, 'First Year Merit Award',       2500, 2024);
