import os


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
                raise FileNotFoundError(f"Model file not found: {self.model_path}")
            from llama_cpp import Llama

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
        output = self.llm(
            prompt,
            max_tokens=150,
            stop=["<|im_end|>", ";", "\n\n"],
            temperature=0.0,
        )
        text = output["choices"][0]["text"].strip()
        # Clean markdown code blocks if the model wrapped it
        text = text.replace("```sql", "").replace("```", "").strip()
        return text
