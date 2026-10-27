from dataclasses import dataclass
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


@dataclass
class FewShotExample:
    question: str
    sql: str


# Default benchmark seed examples
DEFAULT_FEW_SHOT_EXAMPLES = [
    FewShotExample(
        question="List all employees in the Sales department",
        sql="SELECT * FROM employees WHERE department = 'Sales';",
    ),
    FewShotExample(
        question="Find employees earning more than 80000 ordered by salary",
        sql="SELECT name, salary FROM employees WHERE salary > 80000 ORDER BY salary DESC;",
    ),
    FewShotExample(
        question="What is the total sales amount made by each employee?",
        sql="SELECT e.name, SUM(s.amount) AS total_sales FROM employees e JOIN sales s ON e.id = s.employee_id GROUP BY e.id, e.name;",
    ),
    FewShotExample(
        question="Who is the manager of each department?",
        sql="SELECT d.name AS department, e.name AS manager FROM departments d JOIN employees e ON d.manager_id = e.id;",
    ),
    FewShotExample(
        question="Show employees who have not recorded any sales",
        sql="SELECT name FROM employees WHERE id NOT IN (SELECT DISTINCT employee_id FROM sales WHERE employee_id IS NOT NULL);",
    ),
    FewShotExample(
        question="Count the number of employees in each department",
        sql="SELECT department, COUNT(*) AS num_employees FROM employees GROUP BY department;",
    ),
]


class FewShotRetriever:
    """Retrieves the top-k most semantically similar question-SQL pairs using TF-IDF."""

    def __init__(
        self,
        examples: list[FewShotExample] | None = None,
        top_k: int = 2,
    ):
        self.examples = examples or list(DEFAULT_FEW_SHOT_EXAMPLES)
        self.top_k = top_k
        self.vectorizer = TfidfVectorizer(stop_words="english")
        self._fit()

    def _fit(self):
        corpus = [ex.question for ex in self.examples]
        if corpus:
            self.tfidf_matrix = self.vectorizer.fit_transform(corpus)
        else:
            self.tfidf_matrix = None

    def add_example(self, question: str, sql: str):
        self.examples.append(FewShotExample(question=question, sql=sql))
        self._fit()

    def retrieve(self, question: str) -> list[FewShotExample]:
        """Returns the top_k most relevant few-shot examples for the question."""
        if not self.examples or self.tfidf_matrix is None:
            return []

        q_vec = self.vectorizer.transform([question])
        similarities = cosine_similarity(q_vec, self.tfidf_matrix)[0]

        # Get top indices
        top_indices = similarities.argsort()[::-1][: self.top_k]
        # Filter out examples with 0 similarity if there are better ones
        retrieved = [
            self.examples[idx]
            for idx in top_indices
            if similarities[idx] > 0.05 or len(self.examples) <= self.top_k
        ]
        return retrieved

    def format_for_prompt(self, question: str) -> str:
        """Formats top few-shot examples for prompt injection."""
        examples = self.retrieve(question)
        if not examples:
            return ""

        lines = ["-- Similar Example Queries:"]
        for i, ex in enumerate(examples, 1):
            lines.append(f"-- Example {i}: {ex.question}")
            lines.append(f"-- SQL: {ex.sql}")
        return "\n".join(lines)
