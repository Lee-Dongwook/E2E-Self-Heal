/**
 * @e2e-healer/playwright — SPIKE prototype (issue #299)
 *
 * A Playwright Reporter that collects failing tests and shells out to the existing Python
 * `e2e-healer review` engine, then writes an aggregated ReviewReport JSON. It exists to
 * validate the *integration surface* (Python core behind a native-feeling TypeScript
 * Reporter), not to be a production-grade adapter.
 *
 * Usage (in playwright.config.ts):
 *   reporter: [["list"], ["../../integrations/playwright-reporter/src/index.ts", { diffBase: "HEAD~1" }]]
 */
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve, basename, dirname, relative, isAbsolute } from "node:path";
import type {
  FullConfig,
  Reporter,
  TestCase,
  TestResult,
} from "@playwright/test/reporter";

export type ReviewSeverity = "info" | "warning";

export interface ReviewFinding {
  file: string;
  line: number;
  broken_selector: string;
  root_cause: string;
  suggestion: string;
  recommended_selector?: string;
  severity?: ReviewSeverity;
}

export interface ReviewReport {
  schema_version: string;
  kind: "review";
  test_script_path: string;
  findings: ReviewFinding[];
  has_findings: boolean;
}

export interface E2EHealerReporterOptions {
  /** The e2e-healer executable to invoke (or a full command). Default: "e2e-healer". */
  command?: string;
  /** Path to a git diff file to pass via --diff. Takes precedence over diffBase. */
  diffFile?: string;
  /** Git ref to diff against via --diff-base (e.g. a PR base sha). */
  diffBase?: string;
  /** Where to write the aggregated review report. Default: "e2e-healer-review.json". */
  outputFile?: string;
  /** Playwright test-results dir, mapped to E2E_HEALER_TEST_RESULTS_DIR. */
  resultsDir?: string;
}

interface Failure {
  title: string;
  testPath: string; // Playwright-root-relative spec path
  line: number;
  message: string;
  stack: string;
}

/** Reconstruct a raw failure log in the shape `parse_error_log` expects. */
function buildRawLog(f: Failure): string {
  return [
    `Running: ${f.title}`,
    "",
    f.message,
    f.stack,
    `at ${f.testPath}:${f.line}`,
  ].join("\n");
}

/** The CLI writes JSON to stdout, but structlog may emit non-JSON first — find the object. */
function extractJsonLine(stdout: string): string | undefined {
  return stdout
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("{"));
}

function runHealer(
  command: string,
  failure: Failure,
  logFile: string,
  opts: E2EHealerReporterOptions,
  cwd: string,
): ReviewReport | null {
  const args = ["review", failure.testPath, "--log", logFile];
  if (opts.diffFile) {
    args.push("--diff", opts.diffFile);
  } else if (opts.diffBase) {
    args.push("--diff-base", opts.diffBase);
  }
  args.push("--json");

  const env: NodeJS.ProcessEnv = { ...process.env };
  if (opts.resultsDir) {
    env.E2E_HEALER_TEST_RESULTS_DIR = opts.resultsDir;
  }

  const result = spawnSync(command, args, { encoding: "utf8", env, cwd });
  if (result.error) {
    process.stderr.write(`[e2e-healer] launch failed for ${command}: ${result.error.message}\n`);
    return null;
  }
  if (result.status !== 0) {
    process.stderr.write(
      `[e2e-healer] review exited ${result.status} for ${failure.testPath}:\n` +
        `${(result.stderr || "").trim()}\n`,
    );
    return null;
  }

  const jsonLine = extractJsonLine(result.stdout);
  if (!jsonLine) {
    process.stderr.write(`[e2e-healer] no JSON on stdout for ${failure.testPath}\n`);
    return null;
  }
  try {
    return JSON.parse(jsonLine) as ReviewReport;
  } catch (error) {
    process.stderr.write(`[e2e-healer] invalid JSON for ${failure.testPath}: ${String(error)}\n`);
    return null;
  }
}

export default class E2EHealerReporter implements Reporter {
  private readonly command: string;
  private readonly outputFile: string;
  private readonly opts: E2EHealerReporterOptions;
  private readonly failures: Failure[] = [];
  private rootDir = process.cwd();

  constructor(options: E2EHealerReporterOptions = {}) {
    this.command = options.command ?? "e2e-healer";
    this.outputFile = options.outputFile ?? "e2e-healer-review.json";
    this.opts = options;
  }

  onBegin(config: FullConfig): void {
    // Run the engine from the config file's directory — the base all relative
    // paths (specs, diff files, test-results) resolve against. Note `config.rootDir`
    // is actually the *testDir*, not the project dir, so it can't be used as the base.
    this.rootDir = config.configFile ? dirname(resolve(config.configFile)) : process.cwd();
  }

  onTestEnd(test: TestCase, result: TestResult): void {
    if (result.status === "passed" || result.status === "skipped") {
      return;
    }
    const absFile = isAbsolute(test.location.file)
      ? test.location.file
      : resolve(this.rootDir, test.location.file);
    const relPath = relative(this.rootDir, absFile);
    this.failures.push({
      title: test.titlePath().join(" › "),
      testPath: relPath || test.location.file,
      line: test.location.line,
      message: result.error?.message ?? result.status,
      stack: result.error?.stack ?? "",
    });
  }

  onEnd(): void {
    if (this.failures.length === 0) {
      process.stdout.write("[e2e-healer] no failures — nothing to review\n");
      return;
    }

    const tmp = mkdtempSync(join(tmpdir(), "e2e-healer-"));
    const reports: ReviewReport[] = [];
    try {
      for (const failure of this.failures) {
        const logFile = join(tmp, `${basename(failure.testPath)}.log`);
        writeFileSync(logFile, buildRawLog(failure), "utf8");
        const report = runHealer(this.command, failure, logFile, this.opts, this.rootDir);
        if (report) {
          reports.push(report);
        }
      }
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }

    const findings = reports.flatMap((report) => report.findings);
    const aggregate = {
      schema_version: "1.0",
      kind: "review",
      failed_tests: this.failures.length,
      reports,
      has_findings: findings.length > 0,
    };
    writeFileSync(this.outputFile, JSON.stringify(aggregate, null, 2) + "\n", "utf8");
    process.stdout.write(
      `[e2e-healer] wrote ${reports.length}/${this.failures.length} review report(s), ` +
        `${findings.length} finding(s) -> ${this.outputFile}\n`,
    );
  }
}

