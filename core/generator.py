import os


class GeneratorError(Exception):
    """Raised when model loading or generation fails."""

    pass


class SQLGenerator:
    """Wraps the local SmolLM2 GGUF text-to-sql model via llama-cpp-python."""

    def __init__(self, model_path: str, n_threads: int = 6, n_ctx: int = 1024):
        self.model_path = model_path
        self.n_threads = n_threads
        self.n_ctx = n_ctx
        self._llm = None

    @property
    def llm(self):
        if self._llm is None:
            if not os.path.exists(self.model_path):
                raise GeneratorError(
                    f"Model file not found at '{self.model_path}'. "
                    f"Please place 'SmolLM2-135M-text-to-sql.Q8_0.gguf' in the project directory, "
                    f"or set the MODEL_PATH environment variable."
                )
            try:
                from llama_cpp import Llama
            except ImportError as e:
                raise GeneratorError(
                    "The 'llama-cpp-python' package is not installed. "
                    "Install it via: pip install llama-cpp-python"
                ) from e

            self._llm = Llama(
                model_path=self.model_path,
                n_ctx=self.n_ctx,
                n_threads=self.n_threads,
                verbose=False,
            )
        return self._llm

    def _build_prompt(
        self, question: str, schema_ddl: str, feedback: str | None = None
    ) -> str:
        # Standard chat template for SmolLM2 text-to-sql
        prompt = (
            "<|im_start|>system\n"
            "You are a helpful assistant who answers questions about database tables by responding with SQL queries.<|im_end|>\n"
            "<|im_start|>user\n"
            "Given the following database schema:\n"
            f"{schema_ddl}\n\n"
            "Answer the following question with a SQL query:\n"
            f"{question}"
        )
        if feedback:
            prompt += f"\n\n-- Fix this problem with the previous attempt:\n-- {feedback}\n"
        prompt += "<|im_end|>\n<|im_start|>assistant\n"
        return prompt

    def generate(
        self, question: str, schema_ddl: str, feedback: str | None = None
    ) -> str:
        prompt = self._build_prompt(question, schema_ddl, feedback)
        try:
            output = self.llm(
                prompt,
                max_tokens=150,
                stop=["<|im_end|>", ";", "\n\n"],
                temperature=0.0,
            )
        except Exception as e:
            if not isinstance(e, GeneratorError):
                raise GeneratorError(f"Model inference failed: {e}") from e
            raise
        text = output["choices"][0]["text"].strip()
        # Clean markdown code blocks if the model wrapped it
        text = text.replace("```sql", "").replace("```", "").strip()
        return text


class MockGenerator:
    """Mock generator for testing without requiring a 145MB GGUF file or llama-cpp."""

    def __init__(self, fallback_sql: str | None = None):
        self.fallback_sql = fallback_sql or "SELECT * FROM employees;"
        # Map common question keywords to sample queries
        self.canned = {
            "sales": "SELECT e.name, s.amount FROM employees e JOIN sales s ON e.id = s.employee_id WHERE s.amount > 1000;",
            "department": "SELECT e.name, d.name AS dept_name FROM employees e JOIN departments d ON e.department = d.name;",
            "salary": "SELECT name, salary FROM employees WHERE salary > 70000 ORDER BY salary DESC;",
            "engineer": "SELECT name, salary FROM employees WHERE department = 'Engineering';",
            "alice": "SELECT * FROM employees WHERE name LIKE '%Alice%';",
            "bob": "SELECT * FROM employees WHERE name LIKE '%Bob%';",
        }

    def generate(
        self, question: str, schema_ddl: str, feedback: str | None = None
    ) -> str:
        q_lower = question.lower()
        for kw, sql in self.canned.items():
            if kw in q_lower:
                return sql
        return self.fallback_sql
