"""Регрессии shell-скриптов развёртывания (требуют bash, как и CI-раннер)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")


@unittest.skipIf(BASH is None, "bash не установлен")
class DeployScriptsTests(unittest.TestCase):
    def test_shell_syntax(self) -> None:
        scripts = [
            ROOT / "deploy" / "beget_bootstrap.sh",
            *sorted((ROOT / "deploy" / "vps").glob("*.sh")),
        ]
        for script in scripts:
            with self.subTest(script=script.name):
                result = subprocess.run(
                    [str(BASH), "-n", str(script)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_existing_modern_python_is_used_without_build(self) -> None:
        """Найденный системный python3.11+ раньше всё равно проверялся как
        $PREFIX/bin/python3.11 и скрипт падал с кодом 4 «сборка не дала Python»."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            wrapper = bin_dir / "python3"
            wrapper.write_text(
                f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" "$@"\n',
                encoding="utf-8",
            )
            wrapper.chmod(0o755)
            env = dict(os.environ)
            env.update(
                HOME=root.as_posix(),
                PREFIX=(root / "prefix").as_posix(),
                PATH=f"{bin_dir.as_posix()}:/usr/bin:/bin",
            )
            result = subprocess.run(
                [str(BASH), str(ROOT / "deploy" / "beget_bootstrap.sh")],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                env=env, timeout=120,
            )
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            self.assertIn("Подходящий интерпретатор уже есть", result.stdout)


if __name__ == "__main__":
    unittest.main()
