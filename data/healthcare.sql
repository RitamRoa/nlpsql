-- =========================================================
-- Domain: Healthcare & Hospital Management
-- =========================================================

CREATE TABLE hospitals (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    state TEXT NOT NULL,
    bed_count INTEGER NOT NULL,
    type TEXT NOT NULL -- 'General', 'Specialty', 'Teaching', 'Clinic'
);

CREATE TABLE departments (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    hospital_id INTEGER NOT NULL,
    floor INTEGER NOT NULL,
    FOREIGN KEY (hospital_id) REFERENCES hospitals(id)
);

CREATE TABLE doctors (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    specialization TEXT NOT NULL,
    department_id INTEGER NOT NULL,
    years_experience INTEGER NOT NULL,
    license_no TEXT NOT NULL,
    consultation_fee REAL NOT NULL,
    FOREIGN KEY (department_id) REFERENCES departments(id)
);

CREATE TABLE patients (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    dob TEXT NOT NULL,
    gender TEXT NOT NULL, -- 'Male', 'Female', 'Other'
    blood_type TEXT NOT NULL,
    city TEXT NOT NULL,
    insurance_provider TEXT -- NULL means no insurance
);

CREATE TABLE appointments (
    id INTEGER PRIMARY KEY,
    patient_id INTEGER NOT NULL,
    doctor_id INTEGER NOT NULL,
    appointment_date TEXT NOT NULL,
    appointment_time TEXT NOT NULL,
    status TEXT NOT NULL, -- 'Completed', 'Scheduled', 'Cancelled', 'No-Show'
    reason TEXT NOT NULL,
    FOREIGN KEY (patient_id) REFERENCES patients(id),
    FOREIGN KEY (doctor_id) REFERENCES doctors(id)
);

CREATE TABLE admissions (
    id INTEGER PRIMARY KEY,
    patient_id INTEGER NOT NULL,
    hospital_id INTEGER NOT NULL,
    doctor_id INTEGER NOT NULL,
    admit_date TEXT NOT NULL,
    discharge_date TEXT, -- NULL if currently admitted
    ward TEXT NOT NULL, -- 'ICU', 'General', 'Surgery', 'Pediatrics'
    diagnosis TEXT NOT NULL,
    total_bill REAL NOT NULL,
    FOREIGN KEY (patient_id) REFERENCES patients(id),
    FOREIGN KEY (hospital_id) REFERENCES hospitals(id),
    FOREIGN KEY (doctor_id) REFERENCES doctors(id)
);

CREATE TABLE prescriptions (
    id INTEGER PRIMARY KEY,
    appointment_id INTEGER NOT NULL,
    medication TEXT NOT NULL,
    dosage TEXT NOT NULL,
    duration_days INTEGER NOT NULL,
    refills INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (appointment_id) REFERENCES appointments(id)
);

-- Seed Data: Hospitals
INSERT INTO hospitals VALUES (1, 'City General Hospital',       'New York',      'NY', 600, 'General');
INSERT INTO hospitals VALUES (2, 'Lakeside Medical Center',     'Chicago',       'IL', 350, 'Teaching');
INSERT INTO hospitals VALUES (3, 'Sunrise Specialty Clinic',    'Los Angeles',   'CA', 120, 'Specialty');

-- Seed Data: Departments
INSERT INTO departments VALUES (1,  'Cardiology',       1, 3);
INSERT INTO departments VALUES (2,  'Neurology',        1, 4);
INSERT INTO departments VALUES (3,  'Emergency',        1, 1);
INSERT INTO departments VALUES (4,  'Orthopedics',      2, 2);
INSERT INTO departments VALUES (5,  'Pediatrics',       2, 3);
INSERT INTO departments VALUES (6,  'Oncology',         3, 2);
INSERT INTO departments VALUES (7,  'Dermatology',      3, 1);

-- Seed Data: Doctors (10 doctors)
INSERT INTO doctors VALUES (1,  'Dr. Sarah Chen',       'Cardiology',    1,  15, 'LIC-10023', 250.00);
INSERT INTO doctors VALUES (2,  'Dr. Marcus Webb',      'Neurology',     2,  20, 'LIC-10045', 300.00);
INSERT INTO doctors VALUES (3,  'Dr. Priya Sharma',     'Emergency',     3,   8, 'LIC-10067', 200.00);
INSERT INTO doctors VALUES (4,  'Dr. James O''Brien',   'Orthopedics',   4,  12, 'LIC-10089', 275.00);
INSERT INTO doctors VALUES (5,  'Dr. Amina Diallo',     'Pediatrics',    5,  10, 'LIC-10101', 220.00);
INSERT INTO doctors VALUES (6,  'Dr. Robert Kim',       'Oncology',      6,  18, 'LIC-10123', 350.00);
INSERT INTO doctors VALUES (7,  'Dr. Elena Voss',       'Dermatology',   7,   6, 'LIC-10145', 180.00);
INSERT INTO doctors VALUES (8,  'Dr. Carlos Mendez',    'Cardiology',    1,   9, 'LIC-10167', 240.00);
INSERT INTO doctors VALUES (9,  'Dr. Laura Nguyen',     'Neurology',     2,  14, 'LIC-10189', 290.00);
INSERT INTO doctors VALUES (10, 'Dr. Daniel Brooks',    'Pediatrics',    5,   5, 'LIC-10201', 210.00);

-- Seed Data: Patients (12 patients)
INSERT INTO patients VALUES (1,  'Thomas Reed',    '1985-04-12', 'Male',   'O+',  'New York',    'BlueCross');
INSERT INTO patients VALUES (2,  'Maria Santos',   '1992-08-25', 'Female', 'A+',  'Brooklyn',    'Aetna');
INSERT INTO patients VALUES (3,  'Ahmed Hassan',   '1978-11-30', 'Male',   'B+',  'Queens',      NULL);
INSERT INTO patients VALUES (4,  'Jennifer Park',  '2001-02-14', 'Female', 'AB-', 'Chicago',     'BlueCross');
INSERT INTO patients VALUES (5,  'David Liu',      '1965-07-09', 'Male',   'A-',  'Chicago',     'UnitedHealth');
INSERT INTO patients VALUES (6,  'Sara Williams',  '1990-03-21', 'Female', 'O-',  'Los Angeles', 'Aetna');
INSERT INTO patients VALUES (7,  'Michael Torres', '1955-12-01', 'Male',   'B-',  'New York',    'Medicare');
INSERT INTO patients VALUES (8,  'Aisha Johnson',  '1998-09-15', 'Female', 'A+',  'Chicago',     'Medicaid');
INSERT INTO patients VALUES (9,  'Kevin Young',    '1980-06-28', 'Male',   'O+',  'Los Angeles', 'UnitedHealth');
INSERT INTO patients VALUES (10, 'Fatima Al-Sayed','1970-01-17', 'Female', 'AB+', 'New York',    'BlueCross');
INSERT INTO patients VALUES (11, 'Chris Murphy',   '2010-05-30', 'Male',   'A+',  'Chicago',     'Medicaid');
INSERT INTO patients VALUES (12, 'Lily Zhang',     '2008-11-11', 'Female', 'B+',  'Los Angeles', 'UnitedHealth');

-- Seed Data: Appointments (18 appointments)
INSERT INTO appointments VALUES (1,  1, 1,  '2024-01-10', '09:00', 'Completed', 'Chest pain follow-up');
INSERT INTO appointments VALUES (2,  2, 3,  '2024-01-12', '14:00', 'Completed', 'Fever and cough');
INSERT INTO appointments VALUES (3,  3, 2,  '2024-01-15', '10:30', 'Completed', 'Recurring migraines');
INSERT INTO appointments VALUES (4,  4, 5,  '2024-02-01', '11:00', 'Completed', 'Annual checkup');
INSERT INTO appointments VALUES (5,  5, 1,  '2024-02-10', '09:30', 'Completed', 'Hypertension review');
INSERT INTO appointments VALUES (6,  6, 7,  '2024-02-20', '15:00', 'Completed', 'Skin rash consultation');
INSERT INTO appointments VALUES (7,  7, 8,  '2024-03-05', '10:00', 'Completed', 'Post-surgery follow-up');
INSERT INTO appointments VALUES (8,  8, 5,  '2024-03-12', '13:00', 'Completed', 'Asthma management');
INSERT INTO appointments VALUES (9,  9, 6,  '2024-03-20', '11:30', 'Completed', 'Oncology consultation');
INSERT INTO appointments VALUES (10, 10,1,  '2024-04-02', '09:00', 'Completed', 'Arrhythmia evaluation');
INSERT INTO appointments VALUES (11, 11,10, '2024-04-10', '10:00', 'Completed', 'Growth assessment');
INSERT INTO appointments VALUES (12, 12,10, '2024-04-10', '11:00', 'Completed', 'Vaccination');
INSERT INTO appointments VALUES (13, 1, 8,  '2024-05-01', '09:30', 'Completed', 'Echocardiogram review');
INSERT INTO appointments VALUES (14, 3, 9,  '2024-05-15', '10:00', 'Scheduled', 'MRI follow-up');
INSERT INTO appointments VALUES (15, 5, 8,  '2024-06-01', '09:00', 'Scheduled', 'Medication adjustment');
INSERT INTO appointments VALUES (16, 7, 1,  '2024-06-10', '10:30', 'Cancelled', 'Routine checkup');
INSERT INTO appointments VALUES (17, 2, 3,  '2024-06-18', '14:30', 'Scheduled', 'Follow-up visit');
INSERT INTO appointments VALUES (18, 9, 6,  '2024-07-01', '11:00', 'Scheduled', 'Chemotherapy planning');

-- Seed Data: Admissions (8 admissions)
INSERT INTO admissions VALUES (1, 1,  1, 1, '2024-01-10', '2024-01-15', 'General',  'Angina Pectoris',          3200.00);
INSERT INTO admissions VALUES (2, 3,  1, 2, '2024-01-20', '2024-01-25', 'General',  'Severe Migraine',          1800.00);
INSERT INTO admissions VALUES (3, 5,  2, 4, '2024-02-05', '2024-02-12', 'Surgery',  'Hip Replacement Surgery',  12500.00);
INSERT INTO admissions VALUES (4, 7,  1, 8, '2024-03-01', '2024-03-10', 'ICU',      'Cardiac Arrest Recovery',  28000.00);
INSERT INTO admissions VALUES (5, 9,  3, 6, '2024-03-20', '2024-04-05', 'Oncology', 'Chemotherapy Cycle 1',     15000.00);
INSERT INTO admissions VALUES (6, 10, 1, 1, '2024-04-02', '2024-04-04', 'General',  'Atrial Fibrillation',      4500.00);
INSERT INTO admissions VALUES (7, 2,  2, 5, '2024-04-15', NULL,         'Pediatrics','Bronchial Asthma',         2200.00);
INSERT INTO admissions VALUES (8, 11, 2, 5, '2024-04-10', '2024-04-12', 'Pediatrics','Acute Appendicitis',       7800.00);

-- Seed Data: Prescriptions
INSERT INTO prescriptions VALUES (1,  1,  'Aspirin',           '100mg daily',     30, 2);
INSERT INTO prescriptions VALUES (2,  1,  'Atorvastatin',      '20mg nightly',    90, 3);
INSERT INTO prescriptions VALUES (3,  2,  'Amoxicillin',       '500mg 3x daily',  10, 0);
INSERT INTO prescriptions VALUES (4,  3,  'Sumatriptan',       '50mg as needed',  30, 1);
INSERT INTO prescriptions VALUES (5,  5,  'Lisinopril',        '10mg daily',      90, 3);
INSERT INTO prescriptions VALUES (6,  5,  'Metoprolol',        '25mg twice daily', 90, 3);
INSERT INTO prescriptions VALUES (7,  6,  'Hydrocortisone Cream','1% apply twice daily', 30, 0);
INSERT INTO prescriptions VALUES (8,  8,  'Salbutamol Inhaler','2 puffs as needed',60, 2);
INSERT INTO prescriptions VALUES (9,  10, 'Warfarin',          '5mg daily',       90, 3);
INSERT INTO prescriptions VALUES (10, 11, 'Multivitamin',      '1 tablet daily',  90, 1);
INSERT INTO prescriptions VALUES (11, 13, 'Nitroglycerin',     '0.4mg sublingual as needed', 60, 2);
