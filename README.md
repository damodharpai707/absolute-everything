# absolute-everything

This repository includes **Agent Detective Lab**, a Python experiment using Ollama to test whether a tool broker can prevent a worker from publishing private test data even when an AI reviewer gives a misleading verdict.

The [lab documentation](agent-detective-lab/README.md) explains the six trials, broker policy, evidence, and limitations. The private fixture contains a generated fake secret, not a credential from your machine.

## Visual dashboard

The dashboard gives you buttons to connect to Ollama, download Qwen, try the model, check isolation, run tests, and start an experiment. Follow live output and inspect every trial in the evidence viewer. You can open an illustrative example or import a `results.json` downloaded from GitHub Actions without running a model.

![Agent Detective Lab dashboard](docs/dashboard.png)

It uses Python's standard library and local HTML/CSS/JavaScript. No npm, pip packages, remote fonts, or frontend build is needed. The server listens only on `127.0.0.1`; keep its terminal open while using it.

### Open on Windows

Install Python 3.10+ if needed. Clone or update this repository, then **double-click `Start-Dashboard.cmd`**. Alternatively, from the repository folder in PowerShell:

```powershell
py -3 -B agent-detective-lab/dashboard.py
```

Your browser opens at **http://127.0.0.1:8765**. On Windows, you can connect to Windows Ollama, download/try the model, explore/import reports, and open GitHub Actions. Linux-only controls explain that the full experiment and sandbox require Spark/Linux. Opening the GitHub Actions page does not dispatch a workflow; you click **Run workflow** there yourself.

If port 8765 is already occupied, use `--port 8766`. This is a local application; browsing the source on GitHub does not start the Python server.

### Open on Spark from Windows

In PowerShell, create an SSH connection that also forwards the dashboard port:

```powershell
ssh -L 8765:127.0.0.1:8765 YOUR_SPARK_USER@YOUR_SPARK_IP
```

In that SSH terminal, from your Spark checkout:

```bash
cd absolute-everything
git pull --ff-only
python3 -B agent-detective-lab/dashboard.py --no-browser
```

If you cloned under `~/projects`, use `cd ~/projects/absolute-everything` instead. Then open **http://127.0.0.1:8765** in your Windows browser. Keep the SSH terminal open. Python and Ollama run on Spark, and the dashboard travels through the SSH connection. You can also forward port 8765 through VS Code Remote - SSH.

### Use the controls

1. **Check connection** detects Ollama and the exact `qwen2.5:3b` model. Install Ollama first if its executable is missing.
2. **Start Ollama** starts an installed server on localhost with cloud features disabled. An already-running server keeps its own configuration; configure `OLLAMA_NO_CLOUD=1` on that service if needed.
3. **Download model** pulls the model using Ollama. **Try the model** makes a real, short inference request and displays the reply in **Live activity**. These actions do not contact GitHub.
4. On Linux, **Run offline tests** checks the application with simulated services. **Check isolation** tests the real Bubblewrap sandbox.
5. For a live experiment, enter a public repository, open receiver PR number, and scoped GitHub token. Confirm the public writes, then click **Start six-trial experiment**. The underlying lab still enforces its original preflight and broker policy.
6. Open a saved batch in **Evidence**, or use **Import results.json** for GitHub artifacts. Expand a trial for recorded details and use **Export report** to download the report. Imported reports stay in the browser session. The example is explicitly labeled illustrative and makes no model or GitHub calls.

Only one dashboard task runs at a time. Tokens are not written to files, job state, or browser storage; the live-run token is passed to the trusted child process for that run. You can also set `DETECTIVE_GITHUB_TOKEN` in the server environment before launching the dashboard. Stop the dashboard with Ctrl+C; this also stops its active child task and any Ollama process it started. An interrupted experiment may already have created public issues or comments.

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

Evidence artifacts contain `results.json` and `events.jsonl` when the batch reached evidence creation. They are retained for seven days. Public issues and PR comments remain on GitHub. Model installation and CPU inference take time; the job has a 60-minute limit. A red workflow can indicate an incomplete or unexercised trial rather than a setup failure; inspect the results and logs.

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

Expect the offline suite to pass and the isolation command to print `sandbox ready`. The suite now includes the original 13 lab tests plus dashboard/API tests. The model name and local endpoint are fixed in this version. Offline tests simulate external services; they do not establish real model behavior or GitHub permissions. Do not bypass a failing sandbox check.

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
