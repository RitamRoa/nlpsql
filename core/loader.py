import os
from .adapters.base import DBAdapter
from .adapters.sqlite import SQLiteAdapter
from .adapters.duckdb import DuckDBAdapter
from .knowledge import DomainKnowledgeEngine
from .few_shot import FewShotRetriever, FewShotExample
from .retriever import SchemaRetriever
from .agents.classifier import QueryClassifier
from .agents.critic import SemanticCritic
from .agents.drafter import SQLDrafter
from .providers.base import LLMProvider
from .providers.mock import MockProvider
from .pipeline import Pipeline

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


def list_available_schemas() -> list[str]:
    """Returns the names of available .sql domain schemas in the data/ folder."""
    if not os.path.exists(DATA_DIR):
        return []
    schemas = []
    for f in os.listdir(DATA_DIR):
        if f.endswith(".sql"):
            schemas.append(os.path.splitext(f)[0])
    return sorted(schemas)


def load_schema_sql(schema_name_or_path: str = "company") -> str:
    """Loads SQL DDL & seed queries from data/<name>.sql or an explicit file path."""
    # Check if direct file path
    if os.path.exists(schema_name_or_path):
        with open(schema_name_or_path, "r", encoding="utf-8") as f:
            return f.read()

    # Check data/ folder
    candidate = os.path.join(DATA_DIR, f"{schema_name_or_path}.sql")
    if os.path.exists(candidate):
        with open(candidate, "r", encoding="utf-8") as f:
            return f.read()

    raise FileNotFoundError(
        f"Schema file not found for '{schema_name_or_path}'. "
        f"Available schemas: {list_available_schemas()}"
    )


def create_adapter(
    db_engine: str = "sqlite",
    db_path: str | None = None,
    schema_name: str = "company",
) -> DBAdapter:
    """Creates a configured DBAdapter (SQLite or DuckDB).

    Option 2: Loads an in-memory database initialized from data/<schema_name>.sql
    Option 3: Connects directly to an existing database file via db_path (read-only)
    """
    engine = db_engine.lower()

    if db_path:
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"Database file not found: {db_path}")
        if engine == "duckdb":
            return DuckDBAdapter(path=db_path)
        return SQLiteAdapter(path=db_path)

    # In-memory execution seeded from schema SQL
    seed_sql = load_schema_sql(schema_name)
    if engine == "duckdb":
        return DuckDBAdapter(seed_sql=seed_sql)
    return SQLiteAdapter(seed_sql=seed_sql)


def _get_few_shot_examples(schema_name: str) -> list[FewShotExample]:
    """Returns domain-specific few-shot examples for each schema."""
    if schema_name == "company":
        return [
            FewShotExample(
                "List all employees",
                "SELECT * FROM employees;",
            ),
            FewShotExample(
                "List all employees in Engineering",
                "SELECT * FROM employees WHERE department = 'Engineering';",
            ),
            FewShotExample(
                "List all employees in the Sales department",
                "SELECT * FROM employees WHERE department = 'Sales';",
            ),
            FewShotExample(
                "Find employees earning more than 80000 ordered by salary",
                "SELECT name, salary FROM employees WHERE salary > 80000 ORDER BY salary DESC;",
            ),
            FewShotExample(
                "Find employees with salary greater than 70000",
                "SELECT * FROM employees WHERE salary > 70000;",
            ),
            FewShotExample(
                "What is the total sales amount made by each employee?",
                "SELECT e.name, SUM(s.amount) AS total_sales FROM employees e JOIN sales s ON e.id = s.employee_id GROUP BY e.id, e.name;",
            ),
            FewShotExample(
                "Who is the manager of each department?",
                "SELECT d.name AS department, e.name AS manager FROM departments d JOIN employees e ON d.manager_id = e.id;",
            ),
            FewShotExample(
                "Show employees who have not recorded any sales",
                "SELECT name FROM employees WHERE id NOT IN (SELECT DISTINCT employee_id FROM sales WHERE employee_id IS NOT NULL);",
            ),
            FewShotExample(
                "Count the number of employees in each department",
                "SELECT department, COUNT(*) AS num_employees FROM employees GROUP BY department;",
            ),
            FewShotExample(
                "What is the average salary by department?",
                "SELECT department, AVG(salary) as avg_sal FROM employees GROUP BY department;",
            ),
            FewShotExample(
                "What is the average performance rating by department?",
                "SELECT department, ROUND(AVG(performance_rating), 2) AS avg_rating FROM employees GROUP BY department ORDER BY avg_rating DESC;",
            ),
            FewShotExample(
                "Show all remote employees",
                "SELECT * FROM employees WHERE is_remote = 1;",
            ),
            FewShotExample(
                "Which projects are currently active?",
                "SELECT name, budget, status FROM projects WHERE status = 'Active';",
            ),
            FewShotExample(
                "Which projects are currently active and what is their budget?",
                "SELECT name, budget, status FROM projects WHERE status = 'Active';",
            ),
            FewShotExample(
                "How many leave days has each employee taken?",
                "SELECT e.name, SUM(l.days_taken) AS total_leave_days FROM employees e JOIN leaves l ON e.id = l.employee_id WHERE l.approved = 1 GROUP BY e.id, e.name;",
            ),
        ]
    elif schema_name == "ecommerce":
        return [
            FewShotExample(
                "Get all customers",
                "SELECT * FROM customers;",
            ),
            FewShotExample(
                "List all customers",
                "SELECT * FROM customers;",
            ),
            FewShotExample(
                "Show all products",
                "SELECT * FROM products;",
            ),
            FewShotExample(
                "List all Gold tier customers",
                "SELECT name, email, country FROM customers WHERE loyalty_tier = 'Gold';",
            ),
            FewShotExample(
                "What are the top 5 most expensive products?",
                "SELECT name, brand, price FROM products ORDER BY price DESC LIMIT 5;",
            ),
            FewShotExample(
                "Show total revenue by product category",
                "SELECT c.name AS category, SUM(oi.quantity * oi.unit_price) AS revenue FROM order_items oi JOIN products p ON oi.product_id = p.id JOIN categories c ON p.category_id = c.id GROUP BY c.name ORDER BY revenue DESC;",
            ),
            FewShotExample(
                "How many orders has each customer placed?",
                "SELECT c.name, COUNT(o.id) AS order_count FROM customers c LEFT JOIN orders o ON c.id = o.customer_id GROUP BY c.id, c.name;",
            ),
            FewShotExample(
                "Show all delivered orders with customer names",
                "SELECT c.name AS customer, o.id AS order_id, o.total_amount, o.order_date FROM orders o JOIN customers c ON o.customer_id = c.id WHERE o.status = 'Delivered';",
            ),
            FewShotExample(
                "What products have the highest ratings?",
                "SELECT name, brand, rating FROM products ORDER BY rating DESC LIMIT 5;",
            ),
            FewShotExample(
                "Find customers who have never placed an order",
                "SELECT name, email FROM customers WHERE id NOT IN (SELECT DISTINCT customer_id FROM orders);",
            ),
            FewShotExample(
                "Show the average order value by payment method",
                "SELECT payment_method, ROUND(AVG(total_amount), 2) AS avg_order_value FROM orders GROUP BY payment_method ORDER BY avg_order_value DESC;",
            ),
        ]
    elif schema_name == "university":
        return [
            FewShotExample(
                "List all students in Computer Science major",
                "SELECT name, year, gpa FROM students WHERE major = 'Computer Science' ORDER BY gpa DESC;",
            ),
            FewShotExample(
                "Show all courses taught by Dr. Alan Turing",
                "SELECT c.code, c.title, c.credits FROM courses c JOIN instructors i ON c.instructor_id = i.id WHERE i.name = 'Dr. Alan Turing';",
            ),
            FewShotExample(
                "What is the average GPA by major?",
                "SELECT major, ROUND(AVG(gpa), 2) AS avg_gpa FROM students GROUP BY major ORDER BY avg_gpa DESC;",
            ),
            FewShotExample(
                "List students who received scholarships",
                "SELECT s.name, sc.name AS scholarship, sc.amount FROM students s JOIN scholarships sc ON s.id = sc.student_id ORDER BY sc.amount DESC;",
            ),
            FewShotExample(
                "How many students are enrolled in each course?",
                "SELECT c.code, c.title, COUNT(e.id) AS enrollment FROM courses c LEFT JOIN enrollments e ON c.id = e.course_id GROUP BY c.id;",
            ),
            FewShotExample(
                "Show all students who got an A grade",
                "SELECT DISTINCT s.name, c.title, e.semester FROM enrollments e JOIN students s ON e.student_id = s.id JOIN courses c ON e.course_id = c.id WHERE e.grade = 'A';",
            ),
            FewShotExample(
                "Which instructors have the most courses?",
                "SELECT i.name, COUNT(c.id) AS course_count FROM instructors i LEFT JOIN courses c ON i.id = c.instructor_id GROUP BY i.id ORDER BY course_count DESC;",
            ),
        ]
    elif schema_name == "healthcare":
        return [
            FewShotExample(
                "List all cardiologists",
                "SELECT name, years_experience, consultation_fee FROM doctors WHERE specialization = 'Cardiology' ORDER BY years_experience DESC;",
            ),
            FewShotExample(
                "Show all patients currently admitted",
                "SELECT p.name, a.diagnosis, a.ward, a.admit_date FROM admissions a JOIN patients p ON a.patient_id = p.id WHERE a.discharge_date IS NULL;",
            ),
            FewShotExample(
                "How many appointments does each doctor have?",
                "SELECT d.name, COUNT(a.id) AS total_appointments FROM doctors d LEFT JOIN appointments a ON d.id = a.doctor_id GROUP BY d.id ORDER BY total_appointments DESC;",
            ),
            FewShotExample(
                "Find patients with no insurance",
                "SELECT name, city, blood_type FROM patients WHERE insurance_provider IS NULL;",
            ),
            FewShotExample(
                "What is the total bill for each patient from admissions?",
                "SELECT p.name, SUM(a.total_bill) AS total_charges FROM patients p JOIN admissions a ON p.id = a.patient_id GROUP BY p.id, p.name ORDER BY total_charges DESC;",
            ),
            FewShotExample(
                "Show all completed appointments with patient and doctor names",
                "SELECT p.name AS patient, d.name AS doctor, a.appointment_date, a.reason FROM appointments a JOIN patients p ON a.patient_id = p.id JOIN doctors d ON a.doctor_id = d.id WHERE a.status = 'Completed';",
            ),
            FewShotExample(
                "Which medications are most commonly prescribed?",
                "SELECT medication, COUNT(*) AS prescription_count FROM prescriptions GROUP BY medication ORDER BY prescription_count DESC;",
            ),
        ]
    # Generic fallback
    return []


def _register_knowledge_rules(
    schema_name: str,
    knowledge_engine: DomainKnowledgeEngine,
    db_path: str | None,
):
    """Registers domain-specific business rules for each schema."""
    if db_path:
        return  # Skip for physical database files (rules are schema-specific)

    if schema_name == "company":
        knowledge_engine.register_rule(
            keywords=["top earners", "high earners", "highest paid", "high salary"],
            name="High Earner",
            description="Employees with salary >= 90000",
            sql_expression="salary >= 90000",
        )
        knowledge_engine.register_rule(
            keywords=["recent sales", "latest sales", "new sales"],
            name="Recent Sales",
            description="Sales made in March 2024 or later",
            sql_expression="sale_date >= '2024-03-01'",
        )
        knowledge_engine.register_rule(
            keywords=["remote", "work from home", "wfh"],
            name="Remote Employees",
            description="Employees working remotely",
            sql_expression="is_remote = 1",
        )
        knowledge_engine.register_rule(
            keywords=["top performer", "best performer", "high performer", "excellent rating"],
            name="Top Performer",
            description="Employees with performance rating >= 4.5",
            sql_expression="performance_rating >= 4.5",
        )
        knowledge_engine.register_rule(
            keywords=["active project", "ongoing project"],
            name="Active Projects",
            description="Projects with status = 'Active'",
            sql_expression="status = 'Active'",
        )

    elif schema_name == "ecommerce":
        knowledge_engine.register_rule(
            keywords=["vip", "top tier", "gold", "gold customer", "premium customer"],
            name="Gold Customer",
            description="Customers with Gold loyalty tier",
            sql_expression="loyalty_tier = 'Gold'",
        )
        knowledge_engine.register_rule(
            keywords=["new customer", "recent signup", "recently joined"],
            name="New Customer",
            description="Customers who signed up in 2024 or later",
            sql_expression="signup_date >= '2024-01-01'",
        )
        knowledge_engine.register_rule(
            keywords=["cancelled order", "failed order"],
            name="Cancelled Orders",
            description="Orders with status = 'Cancelled'",
            sql_expression="status = 'Cancelled'",
        )
        knowledge_engine.register_rule(
            keywords=["low stock", "running out", "out of stock"],
            name="Low Stock Products",
            description="Products with stock_quantity < 20",
            sql_expression="stock_quantity < 20",
        )
        knowledge_engine.register_rule(
            keywords=["top rated", "highly rated", "best product"],
            name="Highly Rated Products",
            description="Products with rating >= 4.5",
            sql_expression="rating >= 4.5",
        )

    elif schema_name == "university":
        knowledge_engine.register_rule(
            keywords=["dean's list", "honor roll", "high gpa", "honor student", "top student"],
            name="Dean's List",
            description="Students with GPA >= 3.7",
            sql_expression="gpa >= 3.7",
        )
        knowledge_engine.register_rule(
            keywords=["failing", "failed", "poor grade", "f grade"],
            name="Failing Students",
            description="Students with grade F in enrollments",
            sql_expression="grade = 'F'",
        )
        knowledge_engine.register_rule(
            keywords=["senior student", "final year", "graduating"],
            name="Senior Students",
            description="Students in their 4th year (year = 4)",
            sql_expression="year = 4",
        )
        knowledge_engine.register_rule(
            keywords=["full class", "oversubscribed", "waitlist"],
            name="Full Courses",
            description="Courses at or above max enrollment",
            sql_expression="(SELECT COUNT(*) FROM enrollments WHERE course_id = courses.id) >= max_enrollment",
        )

    elif schema_name == "healthcare":
        knowledge_engine.register_rule(
            keywords=["currently admitted", "inpatient", "still in hospital"],
            name="Current Inpatients",
            description="Patients currently admitted (discharge_date IS NULL)",
            sql_expression="discharge_date IS NULL",
        )
        knowledge_engine.register_rule(
            keywords=["uninsured", "no insurance", "self pay"],
            name="Uninsured Patients",
            description="Patients with no insurance provider",
            sql_expression="insurance_provider IS NULL",
        )
        knowledge_engine.register_rule(
            keywords=["experienced doctor", "senior doctor", "veteran doctor"],
            name="Experienced Doctors",
            description="Doctors with 15+ years of experience",
            sql_expression="years_experience >= 15",
        )
        knowledge_engine.register_rule(
            keywords=["expensive admission", "high cost", "costly"],
            name="High-Cost Admissions",
            description="Admissions with total bill >= 10000",
            sql_expression="total_bill >= 10000",
        )
        knowledge_engine.register_rule(
            keywords=["icu", "intensive care", "critical"],
            name="ICU Patients",
            description="Patients admitted to the ICU ward",
            sql_expression="ward = 'ICU'",
        )


def build_pipeline(
    db_engine: str = "sqlite",
    db_path: str | None = None,
    schema_name: str = "company",
    provider: LLMProvider | None = None,
) -> tuple[Pipeline, DBAdapter, DomainKnowledgeEngine]:
    """Builds the complete multi-agent pipeline for a specified schema or physical DB file."""
    adapter = create_adapter(
        db_engine=db_engine, db_path=db_path, schema_name=schema_name
    )
    knowledge_engine = DomainKnowledgeEngine(adapter)

    # Register domain-specific business rules
    _register_knowledge_rules(schema_name, knowledge_engine, db_path)

    llm = provider or MockProvider()
    few_shot_examples = _get_few_shot_examples(schema_name)
    few_shot = FewShotRetriever(examples=few_shot_examples or None, top_k=2)
    drafter = SQLDrafter(provider=llm, few_shot_retriever=few_shot)
    retriever = SchemaRetriever()

    pipeline = Pipeline(
        adapter=adapter,
        generator=drafter,
        retriever=retriever,
        knowledge_engine=knowledge_engine,
        classifier=QueryClassifier(),
        critic=SemanticCritic(),
    )

    return pipeline, adapter, knowledge_engine
