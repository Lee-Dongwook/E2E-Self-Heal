import { useEffect, useState } from "react";

type Health = { status: "ok" | "degraded"; database: boolean };

type HealthState = { kind: "loading" } | { kind: "loaded"; health: Health } | { kind: "error" };

type VersionInfo = {
  api_version?: string;
  version?: string;
  schema_version?: string;
};

type VersionState = { kind: "loading" } | { kind: "loaded"; info: VersionInfo } | { kind: "error" };

function statusTone(state: HealthState): string {
  if (state.kind === "loading") {
    return "border-gray-300 bg-gray-50 text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300";
  }

  if (
    state.kind === "loaded" &&
    state.health.status === "ok" &&
    state.health.database
  ) {
    return "border-green-300 bg-green-50 text-green-800 dark:border-green-800 dark:bg-green-950 dark:text-green-200";
  }

  return "border-red-300 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200";
}

export function App() {
  const [state, setState] = useState<HealthState>({ kind: "loading" });
  const [versionState, setVersionState] = useState<VersionState>({ kind: "loading" });

  useEffect(() => {
    // healthz answers 503 with a body when the database is down, so read the body either way.
    fetch("/api/healthz")
      .then((response) => response.json() as Promise<Health>)
      .then((health) => setState({ kind: "loaded", health }))
      .catch(() => setState({ kind: "error" }));
  }, []);

  useEffect(() => {
    // Try to fetch version info - API and schema versions for footer
    fetch("/api/version")
      .then((res) => {
        if (!res.ok) throw new Error("version endpoint not found");
        return res.json() as Promise<VersionInfo>;
      })
      .then((info) => setVersionState({ kind: "loaded", info }))
      .catch(() => {
        // Fallback to /api/info or /api/healthz if version endpoint doesn't exist
        fetch("/api/info")
          .then((r) => r.json() as Promise<VersionInfo>)
          .then((info) => setVersionState({ kind: "loaded", info }))
          .catch(() => setVersionState({ kind: "error" }));
      });
  }, []);

  return (
    <div className="min-h-screen flex flex-col">
      <main className="mx-auto max-w-xl p-8 font-sans flex-1">
        <h1 className="text-2xl font-semibold">E2E Self-Heal</h1>
        <p
          className={`mt-4 rounded-lg border p-4 ${statusTone(
            state
          )}`}
          role="status"
          aria-live="polite"
        >
          {state.kind === "loading" && "Checking the API..."}
          {state.kind === "error" && "The API is not reachable."}
          {state.kind === "loaded" &&
            `API: ${state.health.status} · database: ${
              state.health.database ? "reachable" : "unreachable"
            }`}
        </p>
      </main>

      <footer className="mt-auto border-t border-gray-200 dark:border-gray-800 py-4 px-8 text-center text-sm text-gray-500 dark:text-gray-400">
        {versionState.kind === "loading" && <span>Loading versions...</span>}
        {versionState.kind === "error" && <span>API: unknown · Schema: unknown</span>}
        {versionState.kind === "loaded" && (
          <span>
            API: {versionState.info.api_version || versionState.info.version || "unknown"} · Schema: {versionState.info.schema_version || "unknown"}
          </span>
        )}
      </footer>
    </div>
  );
}
