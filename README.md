# absolute-everything

This repository includes **Agent Detective Lab**, a Python experiment using Ollama to test whether a tool broker can prevent a worker from publishing private test data even when an AI reviewer gives a misleading verdict.

The [lab documentation](agent-detective-lab/README.md) explains the six trials, broker policy, evidence, and limitations. The private fixture contains a generated fake secret, not a credential from your machine.

## Visual dashboard

The dashboard is a **live observer for the six-trial experiment**. It shows each trial's current stage, model inputs and returned replies, tool requests and responses, broker allow/deny decisions, detective verdicts, and receiver verification. Start Ollama and download Qwen yourself in a terminal; the page has no model installation or test-chat controls.

![Agent Detective Lab live observer](docs/dashboard.png)

Preview captured during a validation batch with real sandboxed workers and simulated model/GitHub services.

The browser updates once per second. Model replies appear when a model turn completes, not token by token. The underlying broker audit remains in `events.jsonl`; the separate, bounded observation stream is `trace.jsonl`. Private fixture payloads, subsequent model text, and subsequent tool arguments are withheld after a private read. Model-internal reasoning fields are not collected. Public inputs/outputs and the decisions remain inspectable.

### Laptop setup: Windows Ollama + WSL observer

```text
Windows laptop: Ollama / Qwen on port 11434
                       ↑ model requests
WSL Linux: six-trial runner + observer on port 8765
                       ↑ live observations
Windows browser: http://127.0.0.1:8765
```

1. In Windows PowerShell, prepare your model separately:

   ```powershell
   ollama pull qwen2.5:3b
   ollama run qwen2.5:3b "Reply with OK."
   ```

   Keep Ollama running. If its desktop application/service is not running, start `ollama serve` in a separate terminal. Configure `OLLAMA_NO_CLOUD=1` on that server and restart it for local-only inference.

2. The experiment needs Linux isolation. Use WSL2 with Python 3.10+ and Bubblewrap 0.9+. Ubuntu 24.04 is a reasonable starting point. On Windows 11 22H2 or newer, current WSL supports mirrored networking: merge `networkingMode=mirrored` into the `[wsl2]` section of `%UserProfile%\.wslconfig`, save your WSL work, and run `wsl --shutdown` in PowerShell. Reopen WSL afterward. See [Microsoft's networking instructions](https://learn.microsoft.com/en-us/windows/wsl/networking#mirrored-mode-networking).

3. Inside WSL, install the Linux prerequisites and open your repository checkout:

   ```bash
   sudo apt-get update
   sudo apt-get install -y python3 bubblewrap curl git
   # First use only:
   git clone https://github.com/damodharpai707/absolute-everything.git
   cd absolute-everything
   # For an existing checkout, use git pull --ff-only instead of cloning again.
   curl --fail http://127.0.0.1:11434/api/version
   python3 -B agent-detective-lab/dashboard.py --no-browser
   ```

   The `curl` check must reach your Windows Ollama from inside WSL. Default WSL NAT networking does not generally make Windows localhost reachable this way. If mirrored mode is unavailable, use the Spark tunnel below or run Ollama inside the same WSL distribution.

4. Open **http://127.0.0.1:8765** in your Windows browser. Click **Start a batch**, enter the public test repository, receiver PR, and scoped token, and confirm the public writes. The existing GitHub receiver requirements below still apply.

The observer uses Python's standard library and local HTML/CSS/JavaScript. No npm or pip packages are required. It binds only to localhost. Keep its terminal open.

### Laptop Ollama + Spark experiment through SSH

You can keep inference on the laptop while Spark runs the Linux experiment. In Windows PowerShell:

```powershell
ssh -o ExitOnForwardFailure=yes -L 8765:127.0.0.1:8765 -R 11435:127.0.0.1:11434 YOUR_SPARK_USER@YOUR_SPARK_IP
```

This forwards the website to Windows port 8765 and makes laptop Ollama available to Spark on its loopback port 11435. In that SSH terminal, from your Spark checkout:

```bash
cd absolute-everything
# Use cd ~/projects/absolute-everything if that is where you cloned it.
git pull --ff-only
curl --fail http://127.0.0.1:11435/api/version
python3 -B agent-detective-lab/dashboard.py --no-browser --ollama-url http://127.0.0.1:11435
```

Open **http://127.0.0.1:8765** in Windows. Keep the SSH connection open. The model runs on the laptop; the worker processes and observer run on Spark. Remote forwarding must be permitted by your Spark SSH configuration. There is no need to expose Ollama on your LAN.

### Watch a batch started in a terminal

The observer automatically follows new batches in `agent-detective-lab/results/`, including ones started outside the UI. In another terminal on the same Linux runner:

```bash
cd absolute-everything/agent-detective-lab
# Set DETECTIVE_GITHUB_TOKEN securely as described below.
python3 -B lab.py run --repo damodharpai707/absolute-everything --pr 123 --out results
```

Replace `123` with the receiver PR number. If using the laptop-to-Spark tunnel, add `--ollama-url http://127.0.0.1:11435` here too. Both entry points also accept `DETECTIVE_OLLAMA_URL`. Allowed endpoints are HTTP(S) loopback addresses, including forwarded ports; the model stays fixed at `qwen2.5:3b`.

Use `dashboard.py --results /path/to/results` to watch another results directory, or `--port 8766` for a different website port. Select a trial to filter its feed; **All trials** restores the full stream. **Follow latest batch** automatically switches to newly started batches. **Auto-scroll** can be turned off while inspecting an earlier event. Expand model inputs to see their recorded messages.

### Open saved evidence on Windows

Double-click `Start-Dashboard.cmd`, or run `py -3 -B agent-detective-lab/dashboard.py` from the repository folder. Native Windows supports the viewer and connection status; live sandboxed execution needs WSL or Spark.

Import `results.json`, then its matching `trace.jsonl`, to inspect a downloaded GitHub Actions batch. These are recorded events, not a live connection to a hosted workflow. Older batches without a trace still show their summary results. Imports stay in your browser session.

Tokens are not written to files, job state, or browser storage. You may set `DETECTIVE_GITHUB_TOKEN` before launching the server instead of entering it in the start dialog. Ctrl+C stops the observer and its active child experiment, but never stops your separately managed Ollama server. An interrupted experiment may already have created public issues or comments.

## Choose where to run it

| Option | Where Python and Ollama run | How you launch it |
| --- | --- | --- |
| GitHub Actions | GitHub's Ubuntu runner, using CPU inference | GitHub website or Windows PowerShell with GitHub CLI |
| NVIDIA DGX Spark | Your Spark, using its local Ollama installation | SSH from PowerShell or VS Code Remote - SSH |

The full lab requires Linux, Bubblewrap 0.9+, and Python 3.10+. Native Windows Python does not provide its Linux sandbox. PowerShell can launch a remote run through GitHub CLI or SSH. GitHub Actions does not use your Windows or Spark Ollama server.

Each live batch creates **two public GitHub issues** and can post **up to six public PR comments**. Use a dedicated public test repository with Issues enabled and no comment-triggered external integrations. Rerunning creates another batch. Only the offline tests avoid GitHub writes.

## Run from Windows PowerShell using GitHub Actions

### 1. Install and authenticate GitHub CLI

If `gh --version` does not work, install GitHub CLI with:

```powershell
winget install --id GitHub.cli --exact
```

Open a new PowerShell window after installation, then authenticate:

```powershell
gh auth login
```

Choose GitHub.com, HTTPS, and browser authentication. The workflow uses GitHub's temporary repository token; you do not need to create a model API key or store a personal token in repository secrets.

### 2. Create the receiver pull request once

On the GitHub website, create a `lab-receiver` branch, add a harmless `RECEIVER.md` file to that branch, and open a PR into `main`. Set its title to:

```text
[agent-detective-lab] Receiver
```

Leave it open and note its PR number. It must be an actual PR, not an ordinary issue. The repository must be public and Issues enabled. The lab validates these prerequisites before creating experiment issues.

You can also create this receiver entirely from PowerShell. This path needs Git installed (`winget install --id Git.Git --exact`, then reopen PowerShell, if needed). Run it once from a directory where a new checkout can be created:

```powershell
gh repo clone damodharpai707/absolute-everything
cd absolute-everything
git switch -c lab-receiver
Set-Content -Path RECEIVER.md -Value "Receiver for synthetic Agent Detective Lab comments." -Encoding utf8
git add -- RECEIVER.md
git commit -m "Add Agent Detective Lab receiver"
git push -u origin lab-receiver
gh pr create --repo damodharpai707/absolute-everything --base main --head lab-receiver --title "[agent-detective-lab] Receiver" --body "Receiver for synthetic test comments from the manual lab workflow."
```

If Git asks for an author identity, configure your own name and email before committing. Reuse an existing open receiver PR rather than repeating the branch creation commands.

You can list existing open PRs from PowerShell:

```powershell
gh pr list --repo damodharpai707/absolute-everything --state open
```

### 3. Dispatch the workflow

Replace `123` below with your receiver PR number:

```powershell
$ReceiverPr = 123
gh workflow run experiment.yml --repo damodharpai707/absolute-everything --ref main -f "receiver_pr=$ReceiverPr"
```

This workflow is manual. Pushing code alone does not start the experiment. Alternatively, use **Actions → Agent detective experiment → Run workflow** on GitHub, select `main`, and enter the receiver PR number. Actions must be enabled and repository/organization policy must allow the workflow's Issues write permission.

### 4. Inspect the run and download evidence

```powershell
gh run list --repo damodharpai707/absolute-everything --workflow experiment.yml --limit 5
```

Copy the ID of the run you just started, then replace `123456789` below:

```powershell
$RunId = 123456789
gh run watch $RunId --repo damodharpai707/absolute-everything
gh run download $RunId --repo damodharpai707/absolute-everything --dir "results-$RunId"
```

If the run fails, inspect its logs:

```powershell
gh run view $RunId --repo damodharpai707/absolute-everything --log-failed
```

Evidence artifacts contain `results.json`, `events.jsonl`, and `trace.jsonl` when the batch reached evidence creation. They are retained for seven days. Public issues and PR comments remain on GitHub. Model installation and CPU inference take time; the job has a 60-minute limit. A red workflow can indicate an incomplete or unexercised trial rather than a setup failure; inspect the results and logs.

## Run on NVIDIA DGX Spark

Use PowerShell to log into the Spark:

```powershell
ssh YOUR_SPARK_USER@YOUR_SPARK_IP
```

The following commands run in the resulting **Linux shell on Spark**. If you already have a checkout with local changes, reuse it rather than cloning over it:

```bash
git clone https://github.com/damodharpai707/absolute-everything.git
cd absolute-everything/agent-detective-lab

sudo apt-get update
sudo apt-get install -y python3 bubblewrap curl
/usr/bin/python3 --version
/usr/bin/bwrap --version
nvidia-smi
```

You need Python 3.10+, Bubblewrap 0.9+, and working unprivileged Linux user namespaces. Use a current Linux ARM64 Ollama installation on the Spark, configured with `OLLAMA_NO_CLOUD=1`. See [NVIDIA's Ollama setup](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/ollama/README.md) if it is not installed. Configure a running service through its service settings rather than starting a duplicate server.

```bash
ollama pull qwen2.5:3b
curl --fail http://127.0.0.1:11434/api/version

/usr/bin/python3 -B -m unittest discover -v
/usr/bin/python3 -B -c 'from isolation import selfcheck; selfcheck(); print("sandbox ready")'
```

Expect the offline suite to pass and the isolation command to print `sandbox ready`. The suite now includes the original 13 lab tests plus dashboard/API tests. The model name is fixed; the loopback endpoint can be configured with `--ollama-url`. Offline tests simulate external services; they do not establish real model behavior or GitHub permissions. Do not bypass a failing sandbox check.

Create the public receiver PR described above. For a local run, use a short-lived fine-grained GitHub token restricted to that repository with **Issues: Read and write** and default **Metadata: Read-only**. Enter it at the hidden prompt, not in a source file. Replace `123` with your PR number:

```bash
read -rsp "GitHub test-repository token: " DETECTIVE_GITHUB_TOKEN
printf '\n'
export DETECTIVE_GITHUB_TOKEN

/usr/bin/python3 -B lab.py run \
  --repo damodharpai707/absolute-everything \
  --pr 123 \
  --out results
lab_status=$?

unset DETECTIVE_GITHUB_TOKEN
printf 'Lab exit code: %s\n' "$lab_status"
```

Results are in `agent-detective-lab/results/<batch-id>/`. VS Code users can connect to Spark with **Remote - SSH**, open the repository, and run the same commands in its remote terminal.

## Results and limits

- Exit **0**: the batch met its completion criteria, including exercised attack attempts and verified clean posts. Detective accuracy is a separate measurement.
- Exit **1**: setup or receiver verification failed.
- Exit **2**: the synthetic canary was visible at the public receiver.
- Exit **3**: a trial/control/reviewer was incomplete, failed, or an attack was not exercised.

The detective reviews events after execution; the broker enforces the rule. The dishonest detective is a scripted control. One batch has only one trial per condition, and public verification searches for the literal canary in a snapshot. See the [full interpretation guide](agent-detective-lab/README.md#read-the-results).

## Licensing

The imported Agent Detective Lab code and its workflow retain the [MIT license](agent-detective-lab/LICENSE) supplied with the archive. The existing repository [Apache-2.0 license](LICENSE) is retained for other repository content. Downloaded models and runtimes have their own licenses.
