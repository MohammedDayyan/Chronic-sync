import os
from huggingface_hub import hf_hub_download

try:
    from llama_cpp import Llama
except ImportError:
    Llama = None
from src.utils.logger import log_info

class LocalHFModel:
    _instance = None

    def __init__(self,repo_id="bartowski/Qwen2.5-7B-Instruct-GGUF",filename="Qwen2.5-7B-Instruct-Q4_K_M.gguf"):
        if Llama is None:
            raise ImportError("llama-cpp-python is required for CPU inference. Install it via pip.")

        log_info(f"Downloading model weights from Hugging Face Hub ({repo_id}/{filename})...")
        model_path = hf_hub_download(repo_id=repo_id,filename=filename)

        log_info("Initializing Llama model instance on CPU (utilizing all physical cores)...")

        self.llm = Llama(
            model_path=model_path,
            n_ctx=4096,
            n_threads=os.cpu_count() or 8,
            verbose=False
        )

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def generate(self,prompt: str,max_tokens: int = 512,temperature: float = 0.1):
        response = self.llm(
            prompt,
            max_tokens = max_tokens,
            temperature=temperature,
            stop = ["```\n\n","User:"],
            echo=False
        )

        return response["choices"][0]["text"].strip()