from .base import LLMProvider


class MockProvider:
    """Mock LLM Provider for testing without network, GPU, or model weights.

    Contains keyword-keyed canned responses for all 4 built-in schemas:
    company, ecommerce, university, healthcare.
    """

    def __init__(self, canned_responses: dict[str, str] | None = None):
        self.canned = canned_responses or {
            # ── Company schema ─────────────────────────────────────────
            "engineer":     "SELECT name, salary, job_title FROM employees WHERE department = 'Engineering';",
            "engineering":  "SELECT name, salary, job_title FROM employees WHERE department = 'Engineering';",
            "sales dept":   "SELECT name, salary FROM employees WHERE department = 'Sales';",
            "department count": "SELECT department, COUNT(*) AS num_employees FROM employees GROUP BY department;",
            "salary":       "SELECT name, salary FROM employees WHERE salary > 70000 ORDER BY salary DESC;",
            "manager":      "SELECT d.name AS department, e.name AS manager FROM departments d JOIN employees e ON d.manager_id = e.id;",
            "count":        "SELECT department, COUNT(*) AS num_employees FROM employees GROUP BY department;",
            "remote":       "SELECT name, department, job_title FROM employees WHERE is_remote = 1;",
            "leave":        "SELECT e.name, SUM(l.days_taken) AS total_leave_days FROM employees e JOIN leaves l ON e.id = l.employee_id WHERE l.approved = 1 GROUP BY e.id, e.name ORDER BY total_leave_days DESC;",
            "project":      "SELECT p.name, p.status, p.budget, d.name AS department FROM projects p JOIN departments d ON p.department_id = d.id ORDER BY p.status;",
            "performance":  "SELECT name, department, performance_rating FROM employees ORDER BY performance_rating DESC;",
            "budget":       "SELECT name, budget, location FROM departments ORDER BY budget DESC;",
            "region":       "SELECT region, SUM(amount) AS total_sales FROM sales GROUP BY region ORDER BY total_sales DESC;",
            "product line": "SELECT product_line, SUM(amount) AS revenue FROM sales GROUP BY product_line ORDER BY revenue DESC;",

            # ── Ecommerce schema ────────────────────────────────────────
            "gold":         "SELECT name, email, country, total_spent FROM customers WHERE loyalty_tier = 'Gold';",
            "silver":       "SELECT name, email, country FROM customers WHERE loyalty_tier = 'Silver';",
            "order":        "SELECT c.name AS customer, o.id, o.total_amount, o.status, o.order_date FROM orders o JOIN customers c ON o.customer_id = c.id ORDER BY o.order_date DESC;",
            "product":      "SELECT name, brand, price, rating, stock_quantity FROM products ORDER BY rating DESC;",
            "stock":        "SELECT name, brand, stock_quantity FROM products ORDER BY stock_quantity ASC;",
            "review":       "SELECT p.name AS product, r.rating, r.review_text, c.name AS reviewer FROM reviews r JOIN products p ON r.product_id = p.id JOIN customers c ON r.customer_id = c.id ORDER BY r.rating DESC;",
            "category":     "SELECT cat.name AS category, COUNT(p.id) AS product_count FROM categories cat LEFT JOIN products p ON cat.id = p.category_id GROUP BY cat.name;",
            "revenue":      "SELECT SUM(oi.quantity * oi.unit_price * (1 - oi.discount/100.0)) AS total_revenue FROM order_items oi;",
            "cancelled":    "SELECT c.name AS customer, o.id, o.total_amount, o.order_date FROM orders o JOIN customers c ON o.customer_id = c.id WHERE o.status = 'Cancelled';",
            "payment":      "SELECT payment_method, COUNT(*) AS num_orders, ROUND(AVG(total_amount), 2) AS avg_value FROM orders GROUP BY payment_method;",
            "discount":     "SELECT p.name, oi.discount FROM order_items oi JOIN products p ON oi.product_id = p.id WHERE oi.discount > 0 ORDER BY oi.discount DESC;",
            "brand":        "SELECT brand, COUNT(*) AS products, ROUND(AVG(price), 2) AS avg_price FROM products GROUP BY brand ORDER BY avg_price DESC;",

            # ── University schema ───────────────────────────────────────
            "student":      "SELECT name, major, year, gpa FROM students ORDER BY gpa DESC;",
            "course":       "SELECT c.code, c.title, c.credits, i.name AS instructor FROM courses c JOIN instructors i ON c.instructor_id = i.id;",
            "instructor":   "SELECT i.name, i.title, d.name AS department FROM instructors i JOIN departments d ON i.department_id = d.id;",
            "professor":    "SELECT name, title, email FROM instructors WHERE title = 'Professor';",
            "gpa":          "SELECT name, major, gpa FROM students WHERE gpa IS NOT NULL ORDER BY gpa DESC;",
            "grade":        "SELECT s.name AS student, c.title AS course, e.grade, e.semester FROM enrollments e JOIN students s ON e.student_id = s.id JOIN courses c ON e.course_id = c.id ORDER BY e.semester;",
            "scholarship":  "SELECT s.name AS student, sc.name AS scholarship, sc.amount FROM students s JOIN scholarships sc ON s.id = sc.student_id ORDER BY sc.amount DESC;",
            "enrollment":   "SELECT c.code, c.title, COUNT(e.id) AS enrolled FROM courses c LEFT JOIN enrollments e ON c.id = e.course_id GROUP BY c.id ORDER BY enrolled DESC;",
            "attendance":   "SELECT s.name, c.title, e.attendance_pct FROM enrollments e JOIN students s ON e.student_id = s.id JOIN courses c ON e.course_id = c.id WHERE e.attendance_pct IS NOT NULL ORDER BY e.attendance_pct ASC;",
            "major":        "SELECT major, COUNT(*) AS student_count, ROUND(AVG(gpa), 2) AS avg_gpa FROM students GROUP BY major ORDER BY avg_gpa DESC;",

            # ── Healthcare schema ───────────────────────────────────────
            "doctor":       "SELECT name, specialization, years_experience, consultation_fee FROM doctors ORDER BY years_experience DESC;",
            "patient":      "SELECT name, gender, blood_type, city, insurance_provider FROM patients;",
            "appointment":  "SELECT p.name AS patient, d.name AS doctor, a.appointment_date, a.status, a.reason FROM appointments a JOIN patients p ON a.patient_id = p.id JOIN doctors d ON a.doctor_id = d.id ORDER BY a.appointment_date DESC;",
            "admission":    "SELECT p.name AS patient, ad.diagnosis, ad.ward, ad.admit_date, ad.discharge_date, ad.total_bill FROM admissions ad JOIN patients p ON ad.patient_id = p.id ORDER BY ad.admit_date DESC;",
            "prescription": "SELECT p.name AS patient, pr.medication, pr.dosage, pr.duration_days FROM prescriptions pr JOIN appointments a ON pr.appointment_id = a.id JOIN patients p ON a.patient_id = p.id;",
            "diagnosis":    "SELECT p.name AS patient, ad.diagnosis, ad.ward, ad.total_bill FROM admissions ad JOIN patients p ON ad.patient_id = p.id ORDER BY ad.total_bill DESC;",
            "specialist":   "SELECT specialization, COUNT(*) AS doctor_count, ROUND(AVG(consultation_fee), 2) AS avg_fee FROM doctors GROUP BY specialization ORDER BY avg_fee DESC;",
            "hospital":     "SELECT name, city, state, bed_count, type FROM hospitals;",
            "insurance":    "SELECT insurance_provider, COUNT(*) AS patient_count FROM patients WHERE insurance_provider IS NOT NULL GROUP BY insurance_provider ORDER BY patient_count DESC;",
            "icu":          "SELECT p.name AS patient, ad.diagnosis, ad.admit_date, ad.total_bill FROM admissions ad JOIN patients p ON ad.patient_id = p.id WHERE ad.ward = 'ICU';",
            "medication":   "SELECT medication, COUNT(*) AS prescription_count FROM prescriptions GROUP BY medication ORDER BY prescription_count DESC;",
            "uninsured":    "SELECT name, city, blood_type FROM patients WHERE insurance_provider IS NULL;",
        }
        self.call_history: list[str] = []

    def generate_text(
        self,
        prompt: str,
        stop: list[str] | None = None,
        max_tokens: int = 150,
        temperature: float = 0.0,
    ) -> str:
        self.call_history.append(prompt)
        p_lower = prompt.lower()

        # Prioritize matching keywords in the actual Question portion of the prompt
        q_part = p_lower
        if "### question:" in p_lower:
            q_part = p_lower.split("### question:")[1].split("###")[0]
        elif "question:" in p_lower:
            q_part = p_lower.split("question:")[1].split("\n")[0]

        # Try multi-word keyword matches first (most specific)
        multi_word_keys = [(k, v) for k, v in self.canned.items() if " " in k]
        for kw, sql in multi_word_keys:
            if kw in q_part:
                return sql

        # Then single-word matches in the question
        for kw, sql in self.canned.items():
            if " " not in kw and kw in q_part:
                return sql

        # Fallback: search full prompt
        for kw, sql in self.canned.items():
            if kw in p_lower:
                return sql

        return "SELECT * FROM employees;"
