# Taking over AVP_BlenderPlugin's gates

If you're picking this repository up and we're gone, this says what each gate proves, so you can keep it proving that even if a tool
disappears. The rule we worked by: **a gate that can't run is a failed gate, and the fix is a replacement tool that proves the same
thing, never a skip.** Review any new tool as untrusted (check its hash and signature, read it) before it runs.

| Gate | Where | What it proves | If its tool is gone |
|---|---|---|---|
| No precompiled code | checks.yml | Everything in the repository is source or a resource. Nothing compiled, no bytecode, no archive, judged by each file's first bytes as well as its name | It's plain bash and git: it needs nothing but a shell |
| No scanner told to look away | checks.yml | No comment or settings file silences a scanner. Findings are fixed, never hidden | Plain bash and git. Add the suppression words of any new scanner you add |
| The safety screen | checks.yml | The add-on's own Python never starts a program, opens the network, loads native code, runs text as code, unpickles, or deletes a folder tree | Plain grep. If the add-on ever truly needs one of those, that's a design review first, not an edit to the list |
| No third-party packages | checks.yml | There is nothing outside our own code to check for known vulnerabilities | If a package must come in, add a dependency scanner first (OSV-Scanner, or what replaced it), pinned and hash-checked, and make it fail on any known vulnerability |
| Snyk Code | checks.yml, release.yml | A static analysis of every Python file finds nothing, and it read every file: a skipped file fails | Find a current SAST tool that reads Python, pin and hash-check it, and keep the "every file was read" count check |
| The release zip | release.yml | The zip published is built from the tagged commit's own bytes, and is exactly the zip the local Blender gates tested (the same SHA-256) | tools/release_package.py needs only Python and git |
| VirusTotal | release.yml | About 60-70 antivirus engines looked at the zip, and the draft says what they found | Find a service that does the same; read its answers as hostile, as tools/check_virustotal.py does |
| The local Blender gates | not in GitHub | The plugin imports, exports and refuses bad input correctly in Blender 5.2, headless | Any Blender LTS, downloaded from blender.org and hash-checked, run with --background --factory-startup and its own BLENDER_USER_RESOURCES folder |
