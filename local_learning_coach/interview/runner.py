from __future__ import annotations

import hashlib
import ast
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from local_learning_coach.config import Settings


class CodeRunnerError(RuntimeError):
    """Güvenli biçimde kullanıcıya gösterilebilecek kod çalıştırma hatası."""


@dataclass(frozen=True)
class CodeRunResult:
    status: str
    passed_tests: int
    total_tests: int
    elapsed_ms: int
    output: str
    code_sha256: str
    runner: str
    isolation_notice: str


class CodeRunner(Protocol):
    name: str

    def run(self, code: str, trusted_tests: str, *, approved: bool = False) -> CodeRunResult: ...


class DisabledCodeRunner:
    name = "disabled"

    def run(self, code: str, trusted_tests: str, *, approved: bool = False) -> CodeRunResult:
        raise CodeRunnerError(
            "Yerel kod çalıştırma varsayılan olarak kapalıdır. LLC_CODE_RUNNER_ENABLED=1 ayarı ve açık onay gerekir."
        )


class LocalPythonRunner:
    name = "local-python-isolated-process"

    def __init__(self, settings: Settings, python_executable: str | None = None):
        self.settings = settings
        self.python_executable = str(Path(python_executable or sys.executable).resolve())

    @staticmethod
    def _validate_source(code: str) -> None:
        """Dosya/ağ/process erişimi veren Python yüzeylerini çalıştırmadan önce reddeder."""
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError:
            return  # Syntax hatası izole süreçte ölçülebilir sonuç olarak raporlanır.
        forbidden_nodes = (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal, ast.ClassDef)
        forbidden_names = {
            "open", "exec", "eval", "compile", "__import__", "input", "breakpoint", "help",
            "globals", "locals", "vars", "getattr", "setattr", "delattr", "dir", "memoryview",
        }
        for node in ast.walk(tree):
            if isinstance(node, forbidden_nodes):
                raise CodeRunnerError("Kod görevi import, sınıf veya süreç/dosya erişimi içermemelidir.")
            if isinstance(node, ast.Name) and (node.id in forbidden_names or node.id.startswith("__")):
                raise CodeRunnerError(f"Kod görevi güvenli olmayan bir ad kullanıyor: {node.id}")
            if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
                raise CodeRunnerError("Kod görevi özel/dunder nesne erişimi içeremez.")

    def run(self, code: str, trusted_tests: str, *, approved: bool = False) -> CodeRunResult:
        if not self.settings.code_runner_enabled or not approved:
            raise CodeRunnerError("Kod çalıştırma hem ortam ayarı hem kullanıcı onayı olmadan başlatılamaz.")
        encoded = code.encode("utf-8")
        if not encoded or len(encoded) > self.settings.code_runner_max_source_bytes:
            raise CodeRunnerError("Kod boş veya izin verilen kaynak boyutu sınırının dışında.")
        if len(trusted_tests.encode("utf-8")) > self.settings.code_runner_max_source_bytes:
            raise CodeRunnerError("Güvenilir test paketi boyut sınırını aşıyor.")
        self._validate_source(code)
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="llc-interview-code-") as directory:
            root = Path(directory).resolve()
            solution = root / "solution.py"
            harness = root / "runner.py"
            solution.write_text(code, encoding="utf-8")
            harness.write_text(
                "import contextlib,json,pathlib,traceback\n"
                "class BoundedWriter:\n"
                "  def __init__(self,limit): self.limit=limit; self.parts=[]; self.size=0\n"
                "  def write(self,value):\n"
                "    value=str(value); remaining=max(0,self.limit-self.size); piece=value[:remaining]; self.parts.append(piece); self.size+=len(piece); return len(value)\n"
                "  def flush(self): pass\n"
                "  def getvalue(self): return ''.join(self.parts)\n"
                f"buffer=BoundedWriter({int(self.settings.code_runner_max_output_bytes)!r})\n"
                "safe={'abs':abs,'all':all,'any':any,'bool':bool,'dict':dict,'enumerate':enumerate,'float':float,"
                "'int':int,'len':len,'list':list,'max':max,'min':min,'range':range,'reversed':reversed,'set':set,"
                "'sorted':sorted,'str':str,'sum':sum,'tuple':tuple,'zip':zip,'Exception':Exception,"
                "'ValueError':ValueError,'TypeError':TypeError,'RuntimeError':RuntimeError,'print':print}\n"
                "ns={'__builtins__':safe}\n"
                f"tests={trusted_tests!r}\n"
                "test_items=[t for t in tests.split('\\n---TEST---\\n') if t.strip()]\n"
                "passed=0; errors=[]; status='failed'\n"
                "try:\n"
                "  with contextlib.redirect_stdout(buffer),contextlib.redirect_stderr(buffer):\n"
                "    source=pathlib.Path('solution.py').read_text(encoding='utf-8')\n"
                "    exec(compile(source,'solution.py','exec'),ns)\n"
                "    for test in test_items:\n"
                "      try: exec(compile(test,'trusted_test','exec'),ns); passed+=1\n"
                "      except Exception as exc: errors.append(f'{type(exc).__name__}: {exc}')\n"
                "  status='passed' if passed==len(test_items) else 'failed'\n"
                "except SyntaxError as exc: status='syntax_error'; errors.append(f'SyntaxError: {exc.msg}')\n"
                "except Exception as exc: status='runtime_error'; errors.append(f'{type(exc).__name__}: {exc}')\n"
                "print(json.dumps({'status':status,'passed':passed,'total':len(test_items),'errors':errors,'output':buffer.getvalue()}))\n",
                encoding="utf-8",
            )
            minimal_env = {
                "PYTHONIOENCODING": "utf-8",
                "PYTHONDONTWRITEBYTECODE": "1",
                "TEMP": str(root),
                "TMP": str(root),
            }
            if os.name == "nt" and os.environ.get("SystemRoot"):
                minimal_env["SystemRoot"] = os.environ["SystemRoot"]
            try:
                completed = subprocess.run(
                    [self.python_executable, "-I", str(harness)],
                    cwd=root,
                    env=minimal_env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.settings.code_runner_timeout_seconds,
                    shell=False,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise CodeRunnerError("Kod çalışma süresi sınırını aştı ve durduruldu.") from exc
            output = completed.stderr[: self.settings.code_runner_max_output_bytes]
            passed = 0
            total = 0
            status = "runtime_error"
            try:
                import json
                payload = json.loads(completed.stdout.splitlines()[-1])
                passed, total = int(payload["passed"]), int(payload["total"])
                status = str(payload.get("status", "runtime_error"))
                output = (str(payload.get("output", "")) + "\n" + "\n".join(payload.get("errors", []))).strip()
                output = output[: self.settings.code_runner_max_output_bytes]
            except (ValueError, KeyError, IndexError):
                status = "runtime_error"
            files = [item for item in root.rglob("*") if item.is_file()]
            if len(files) > 25 or sum(item.stat().st_size for item in files) > self.settings.code_runner_max_source_bytes * 3:
                raise CodeRunnerError("Kod çalışma alanı dosya sınırını aştı.")
        return CodeRunResult(
            status=status,
            passed_tests=passed,
            total_tests=total,
            elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
            output=output,
            code_sha256=hashlib.sha256(encoded).hexdigest(),
            runner=self.name,
            isolation_notice=(
                "Ayrı geçici süreç, süre ve çıktı sınırı kullanılır; işletim sistemi düzeyinde ağ izolasyonu garanti edilmez."
            ),
        )


def configured_code_runner(settings: Settings) -> CodeRunner:
    return LocalPythonRunner(settings) if settings.code_runner_enabled else DisabledCodeRunner()
