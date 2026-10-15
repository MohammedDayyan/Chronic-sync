import os
import json
import re
from typing import Optional
from huggingface_hub import hf_hub_download

try:
    from llama_cpp import Llama
except ImportError:
    Llama = None

from src.utils.logger import log_info


class LocalHFModel:
    """Dynamic LLM inference engine supporting CPU-optimized GGUF models with Hugging Face Hub

    downloading and configurable parameters.
    """
    _instance = None

    def __init__(
        self,
        repo_id: Optional[str] = None,
        filename: Optional[str] = None,
        n_ctx: Optional[int] = None,
        n_threads: Optional[int] = None,
        mock: bool = False
    ):
        self.mock = mock or os.getenv("CHRONICLE_MOCK_LLM", "false").lower() in ("1", "true", "yes")
        self.repo_id = repo_id or os.getenv("CHRONICLE_MODEL_REPO", "bartowski/Qwen2.5-7B-Instruct-GGUF")
        self.filename = filename or os.getenv("CHRONICLE_MODEL_FILE", "Qwen2.5-7B-Instruct-Q4_K_M.gguf")
        self.n_ctx = n_ctx or int(os.getenv("CHRONICLE_N_CTX", "4096"))
        self.n_threads = n_threads or int(os.getenv("CHRONICLE_N_THREADS", str(os.cpu_count() or 8)))

        if self.mock:
            log_info("LocalHFModel initialized in Mock / Fast Simulation Mode.")
            self.llm = None
            return

        if Llama is None:
            raise ImportError("llama-cpp-python is required for CPU inference. Install it via pip.")

        log_info(f"Downloading/resolving model weights from Hugging Face Hub ({self.repo_id}/{self.filename})...")
        model_path = hf_hub_download(repo_id=self.repo_id, filename=self.filename)

        log_info(f"Initializing Llama model instance on CPU (threads={self.n_threads}, n_ctx={self.n_ctx})...")
        self.llm = Llama(
            model_path=model_path,
            n_ctx=self.n_ctx,
            n_threads=self.n_threads,
            verbose=False
        )

    @classmethod
    def get_instance(cls, **kwargs):
        if cls._instance is None:
            cls._instance = cls(**kwargs)
        return cls._instance

    @classmethod
    def reset_instance(cls, **kwargs):
        cls._instance = cls(**kwargs) if kwargs else None
        return cls._instance

    def _generate_mock_response(self, prompt: str) -> str:
        """Generates deterministic mock responses for testing without downloading weights."""
        prompt_lower = prompt.lower()
        if "technical documentation auditor" in prompt_lower or "has_drift" in prompt_lower:
            # Extract target or filename if available
            target_match = re.search(r"Target:\s*(\w+)", prompt)
            target = target_match.group(1) if target_match else "code component"
            return json.dumps({
                "has_drift": True,
                "discrepancy": f"Documentation is out of sync with new signature and parameter updates for '{target}'.",
                "severity": "HIGH"
            })
        elif "rewrite" in prompt_lower or "update the documentation" in prompt_lower or "corrected text:" in prompt_lower:
            target_match = re.search(r"Target Symbol:\s*(\w+)", prompt)
            sig_match = re.search(r"New/Updated Signature:\s*([^\n]+)", prompt)
            target = target_match.group(1) if target_match else "component"
            sig = sig_match.group(1).strip() if sig_match else f"def {target}(...):"
            
            return f"### `{target}`\n\nUpdated signature:\n```python\n{sig}\n```\n\nAutomatically updated to reflect recent structural code changes."
        return "Documentation update complete."

    def generate(
        self,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.1,
        stop: Optional[list] = None
    ) -> str:
        if self.mock or self.llm is None:
            return self._generate_mock_response(prompt)

        stop_sequences = stop or ["```\n\n", "User:", "Assistant:"]
        response = self.llm(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop_sequences,
            echo=False
        )

        return response["choices"][0]["text"].strip()