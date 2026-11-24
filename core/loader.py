import os
from .adapters.base import DBAdapter
from .adapters.sqlite import SQLiteAdapter
from .adapters.duckdb import DuckDBAdapter
from .knowledge import DomainKnowledgeEngine
from .semantic_layer import SemanticLayer, load_semantic_layer
from .few_shot import FewShotRetriever, FewShotExample
from .feedback import FeedbackStore
from .retriever import SchemaRetriever
from .agents.classifier import QueryClassifier
from .agents.critic import SemanticCritic
from .agents.drafter import SQLDrafter
from .providers.base import LLMProvider
from .providers.mock import MockProvider
from .pipeline import Pipeline

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
SEMANTIC_DIR = os.path.join(DATA_DIR, "semantic")


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


def _load_schema_semantic_layer(
    schema_name: str,
    db_path: str | None,
) -> SemanticLayer:
    """Load the editable semantic catalog for a built-in schema, if present."""
    if db_path:
        return SemanticLayer()
    path = os.path.join(SEMANTIC_DIR, f"{schema_name}.yaml")
    if not os.path.exists(path):
        return SemanticLayer()
    return load_semantic_layer(path)


def build_pipeline(
    db_engine: str = "sqlite",
    db_path: str | None = None,
    schema_name: str = "company",
    provider: LLMProvider | None = None,
    include_feedback: bool = True,
) -> tuple[Pipeline, DBAdapter, DomainKnowledgeEngine]:
    """Builds the complete multi-agent pipeline for a specified schema or physical DB file.

    include_feedback=False keeps locally saved user corrections out of the few-shot
    pool. Benchmarks must pass it, or results depend on whoever ran them.
    """
    adapter = create_adapter(
        db_engine=db_engine, db_path=db_path, schema_name=schema_name
    )
    try:
        semantic_layer = _load_schema_semantic_layer(schema_name, db_path)
        semantic_layer.validate_against(adapter.get_schema())
    except Exception:
        adapter.close()
        raise
    knowledge_engine = DomainKnowledgeEngine(adapter, semantic_layer=semantic_layer)

    llm = provider or MockProvider()
    few_shot_examples = _get_few_shot_examples(schema_name)
    if include_feedback:
        few_shot_examples.extend(FeedbackStore().examples(schema_name))
    few_shot = FewShotRetriever(examples=few_shot_examples, top_k=2)
    drafter = SQLDrafter(provider=llm, few_shot_retriever=few_shot)
    retriever = SchemaRetriever(semantic_layer=semantic_layer)

    pipeline = Pipeline(
        adapter=adapter,
        generator=drafter,
        retriever=retriever,
        knowledge_engine=knowledge_engine,
        classifier=QueryClassifier(),
        critic=SemanticCritic(),
        confidence_samples=int(os.environ.get("NLPSQL_CONFIDENCE_SAMPLES", "3")),
    )

    return pipeline, adapter, knowledge_engine
