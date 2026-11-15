"""Run the actual release scan step in Bash with local fake curl responses."""

import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import quote
import zipfile


ROOT = Path(__file__).resolve().parent.parent
VERSION = "1.0.0-alpha.0"
ANALYSIS_ID = "a+b/c=="
STATS = {"malicious": 0, "suspicious": 0, "undetected": 55, "harmless": 5}

FAKE_CURL = r'''#!/usr/bin/env python3
import json
from pathlib import Path
import sys

home = Path(__file__).resolve().parent
plan = json.loads((home / "plan.json").read_text(encoding="utf-8"))
state_file = home / "calls.json"
calls = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else []
args = sys.argv[1:]
if "-o" not in args or args[args.index("-o") + 1] != "release/vt-answer.json":
    raise SystemExit("fake curl refuses an unexpected output path")
reply = plan[len(calls)]
url = args[-1]
if url != reply["url"] or "\n" in url or "\r" in url:
    raise SystemExit("fake curl refuses an unexpected or multiline URL: " + repr(url))
calls.append(url)
state_file.write_text(json.dumps(calls), encoding="utf-8")
(Path.cwd() / "release" / "vt-answer.json").write_text(json.dumps(reply["body"]), encoding="utf-8")
print(reply["code"], end="")
'''


def scan_script():
    lines = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8").splitlines()
    start = lines.index("      - name: Ask VirusTotal about the zip")
    start = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |") + 1
    body = []
    for line in lines[start:]:
        if line.startswith("          "):
            body.append(line[10:])
        elif not line.strip():
            body.append("")
        else:
            break
    return "\n".join(body) + "\n"


def bash_executable():
    if os.name == "nt":
        git = shutil.which("git")
        candidate = Path(git).parent.parent / "bin" / "bash.exe" if git else None
        if candidate is not None and candidate.is_file():
            return str(candidate)
    else:
        candidate = shutil.which("bash")
        if candidate:
            return candidate
    raise RuntimeError("The local release proof requires installed Bash")


def python_executable():
    executable = Path(sys.executable)
    if "blender" in executable.name.lower():
        executable = Path(sys.prefix) / "bin" / ("python.exe" if os.name == "nt" else "python3")
    if not executable.is_file():
        raise RuntimeError("The local release proof requires Blender's Python interpreter")
    return executable


def bash_path(path):
    text = path.as_posix()
    return "/" + text[0].lower() + text[2:] if os.name == "nt" else text


class ScanWorkflow(unittest.TestCase):
    def exercise(self, mode, missing_answer=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "release").mkdir()
            (root / "tools").mkdir()
            binary = root / "fake-bin"
            binary.mkdir()
            shutil.copyfile(ROOT / "tools" / "check_virustotal.py", root / "tools" / "check_virustotal.py")
            archive = root / "release" / f"avp_blender_plugin-{VERSION}.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("proof.txt", "local fake curl proof")
            wanted = hashlib.sha256(archive.read_bytes()).hexdigest()
            lookup = {"data": {"type": "file", "id": wanted,
                               "attributes": {"sha256": wanted, "last_analysis_date": 1763100000,
                                              "last_analysis_stats": STATS}}}
            base = "https://www.virustotal.com/api/v3/"
            if mode in ("upload", "unanalysed"):
                first = {"error": {"code": "NotFoundError"}}
                code = "404"
                if mode == "unanalysed":
                    first = {"data": {"type": "file", "id": wanted, "attributes": {"sha256": wanted}}}
                    code = "200"
                encoded_url = base + "analyses/" + quote(ANALYSIS_ID, safe="")
                plan = [
                    {"url": base + "files/" + wanted, "code": code, "body": first},
                    {"url": base + "files", "code": "200",
                     "body": {"data": {"type": "analysis", "id": ANALYSIS_ID}}},
                    {"url": encoded_url, "code": "200",
                     "body": {"data": {"type": "analysis", "id": ANALYSIS_ID, "attributes": {"status": "queued"}}}},
                    {"url": encoded_url, "code": "200",
                     "body": {"meta": {"file_info": {"sha256": wanted}},
                              "data": {"type": "analysis", "id": ANALYSIS_ID,
                                       "attributes": {"status": "completed", "stats": STATS}}}},
                ]
            else:
                if mode == "refused":
                    lookup["data"]["id"] = "b" * 64
                plan = [{"url": base + "files/" + wanted, "code": "200", "body": lookup}]
            (binary / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
            (binary / "curl").write_text(FAKE_CURL, encoding="utf-8", newline="\n")
            wrapper = "#!/usr/bin/env bash\nset -o pipefail\n"
            if missing_answer:
                wrapper += ('if [ "${2:-}" = "tools/check_virustotal.py" ]; then '
                            "printf 'checker info without an ANSWER\\n'; exit 0; fi\n")
            wrapper += shlex.quote(python_executable().as_posix()) + ' "$@" | tr -d \'\\r\'\n'
            (binary / "python3").write_text(wrapper, encoding="utf-8", newline="\n")
            (binary / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8", newline="\n")
            for name in ("curl", "python3", "sleep"):
                (binary / name).chmod(0o755)
            script = root / "scan.sh"
            script.write_text(scan_script(), encoding="utf-8", newline="\n")
            output = root / "github-output"
            environment = dict(os.environ)
            environment.update({"PATH": str(binary) + os.pathsep + environment.get("PATH", ""),
                                "RUNNER_TEMP": str(root), "GITHUB_OUTPUT": str(output),
                                "VIRUSTOTAL_API_KEY": secrets.token_hex(16), "VERSION": VERSION, "WANTED": wanted})
            bootstrap = ('export PATH="$1:/usr/bin:/bin"; '
                         'for name in curl python3 sleep; do '
                         '[ "$(command -v "$name")" = "$1/$name" ] || exit 125; done; source "$2"')
            result = subprocess.run([bash_executable(), "--noprofile", "--norc", "-c", bootstrap,
                                     "proof-shell", bash_path(binary), bash_path(script)],
                                    cwd=root, env=environment, capture_output=True, text=True, timeout=60)
            outputs = dict(line.split("=", 1) for line in output.read_text().splitlines()) if output.exists() else {}
            calls_file = binary / "calls.json"
            calls = json.loads(calls_file.read_text()) if calls_file.exists() else []
            print(f"CASE {mode}{'-no-answer' if missing_answer else ''}: exit {result.returncode}")
            print("\n".join(result.stderr.splitlines()[-4:]))
            print("STDOUT:", result.stdout.strip())
            print("OUTPUTS:", json.dumps(outputs, sort_keys=True))
            if mode in ("upload", "unanalysed"):
                print("ANALYSIS URL:", encoded_url)
            if mode == "refused" or missing_answer:
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(outputs, {})
                self.assertIn("REFUSED:" if mode == "refused" else "without an ANSWER", result.stderr)
            else:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "", result.stdout)
                self.assertEqual(outputs, {"sha256": wanted, "malicious": "0", "suspicious": "0", "answered": "60"})
                self.assertEqual(calls, [reply["url"] for reply in plan])

    def test_lookup_404_upload_encoded_id_queued_completed(self):
        self.exercise("upload")

    def test_lookup_200_finished_report(self):
        self.exercise("cached")

    def test_checker_refusal_fails_step(self):
        self.exercise("refused")

    def test_lookup_200_not_analysed_uploads(self):
        self.exercise("unanalysed")

    def test_checker_no_answer_fails_closed(self):
        self.exercise("cached", missing_answer=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
