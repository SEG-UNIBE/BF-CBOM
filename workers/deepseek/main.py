import json
import logging
import os
import time

from openai import OpenAI

from common.config import DEEPSEEK_API_KEY
from common.models import JobInstruction, Trace
from common.worker import build_handle_instruction, run_worker

# Derive worker name from env or directory to make cloning simple
NAME = os.getenv("WORKER_NAME") or os.path.basename(os.path.dirname(__file__))

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


class DeepSeekClient:
    def __init__(self, api_key=None):
        token = api_key or DEEPSEEK_API_KEY
        if not token:
            raise OSError("DEEPSEEK_API_KEY not set")
        self.api_key = token
        self.client = OpenAI(api_key=self.api_key, base_url="https://api.deepseek.com")

    def generate_cbom(self, git_url, branch="main"):
        start_time = time.time()
        try:
            system_prompt = (
                "You are a cryptographic component analyzer. "
                "Your task is to analyze a GitHub project and generate a "
                "Cryptographic Bill of Materials (CBOM) following the official CycloneDX standard.\n\n"
                "Identify all cryptographic components including:\n"
                "- Cryptographic algorithms (AES, RSA, SHA256, etc.)\n"
                "- Key management functions\n"
                "- Hashing functions\n"
                "- Digital signatures\n"
                "- Certificates and TLS/SSL usage\n"
                "- Random number generation\n"
                "- Encoding/decoding functions\n\n"
                "Generate the CBOM in valid CycloneDX JSON format with:\n"
                '- bomFormat: "CycloneDX"\n'
                '- specVersion: "1.6"\n'
                "- Proper component types\n"
                "- Comprehensive cryptoProperties for each cryptographic component\n\n"
                "Please only return the formatted JSON without any additional text or markdown. "
                "If there is nothing to report return an empty CBOM."
            )
            user_prompt = (
                f"Please generate me a CBOM json for this project, and "
                f"following the official CycloneDX standard on CBOMs.\n"
                f"Project: {git_url}\n"
                f"Branch: {branch}\n"
                f"Please only return the formatted JSON."
            )
            response = self.client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                stream=False,
            )
            content = response.choices[0].message.content
            if "```json" in content:
                content = content.split("```json")[1].split("```", 1)[0].strip()
            elif "```" in content:
                content = content.split("```", 1)[1].split("```", 1)[0].strip()
            try:
                cbom_data = json.loads(content)
                if not isinstance(cbom_data, dict):
                    if isinstance(cbom_data, list):
                        cbom_data = {"components": cbom_data}
                if "bomFormat" not in cbom_data:
                    cbom_data["bomFormat"] = "CycloneDX"
                if "specVersion" not in cbom_data:
                    cbom_data["specVersion"] = "1.6"
                duration = time.time() - start_time
                return cbom_data, duration, None
            except json.JSONDecodeError as e:
                logger.error("Error parsing JSON response: %s", e)
                logger.error("Response: %s...", content[:500])
                return None, time.time() - start_time, f"json_decode_error: {e}"
        except Exception as e:
            logger.error("Error during CBOM generation: %s", e)
            return None, time.time() - start_time, f"exception: {e}"


def _produce(instr: JobInstruction, trace: Trace) -> str | tuple[str, float]:
    client = DeepSeekClient()
    cbom_data, duration, err = client.generate_cbom(
        git_url=instr.repo_info.git_url, branch=instr.repo_info.branch
    )
    if cbom_data is None:
        raise RuntimeError(err or "deepseek_failed")
    return json.dumps(cbom_data), duration


handle_instruction = build_handle_instruction(NAME, _produce)


def main():
    # Delegate the queue/timeout loop to the shared runner
    # Default timeout 5 minutes for DeepSeek worker
    run_worker(NAME, handle_instruction, default_timeout=300)


if __name__ == "__main__":
    logger.info("starting up...")
    main()
