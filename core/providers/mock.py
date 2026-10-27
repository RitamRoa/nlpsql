from .base import LLMProvider


class MockProvider:
    """Mock LLM Provider for testing without network, GPU, or model weights."""

    def __init__(self, canned_responses: dict[str, str] | None = None):
        self.canned = canned_responses or {
            "engineer": "SELECT name, salary FROM employees WHERE department = 'Engineering';",
            "sales": "SELECT e.name, s.amount FROM employees e JOIN sales s ON e.id = s.employee_id WHERE s.amount > 1000;",
            "department": "SELECT e.name, d.name AS dept_name FROM employees e JOIN departments d ON e.department = d.name;",
            "salary": "SELECT name, salary FROM employees WHERE salary > 70000 ORDER BY salary DESC;",
            "manager": "SELECT d.name, e.name FROM departments d JOIN employees e ON d.manager_id = e.id;",
            "count": "SELECT department, COUNT(*) FROM employees GROUP BY department;",
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

        for kw, sql in self.canned.items():
            if kw in q_part:
                return sql

        for kw, sql in self.canned.items():
            if kw in p_lower:
                return sql

        return "SELECT * FROM employees;"
