import { useI18n } from "../i18n";
import { CodeBlock, Pane, Prov } from "./ui";

// From README "Usage (CI / GitHub Action)".
const WORKFLOW = `- name: E2E self-heal
  id: heal
  uses: Lee-Dongwook/E2E-Self-Heal@v0.6.2
  with:
    test-path: tests/example.spec.ts
    nvidia-api-key: \${{ secrets.NVIDIA_API_KEY }}
    diff-base: \${{ github.event.pull_request.base.sha }}
    app-url: http://localhost:4173

- name: Open patch PR
  if: steps.heal.outputs.outcome == 'healed'
  uses: peter-evans/create-pull-request@v6
  with:
    body-path: \${{ steps.heal.outputs.summary-path }}`;

export function Architecture() {
  const { t } = useI18n();
  return (
    <div className="grid gap-6">
      <div className="flex flex-wrap items-stretch gap-2" aria-label={t.arch.flowLabel}>
        {t.arch.flow.map(([kind, title, desc], i) => (
          <div key={kind + title} className="contents">
            {i > 0 && (
              <span className="self-center font-mono text-muted max-[640px]:hidden" aria-hidden="true">
                →
              </span>
            )}
            <div
              className={`grid min-w-0 flex-[1_1_150px] content-start gap-1 rounded-lg border border-line bg-surface p-3 ${
                kind === "router" ? "border-dashed" : ""
              }`}
            >
              <span className="font-mono text-[0.72rem] text-accent">{kind}</span>
              <b className="text-[0.95rem]">{title}</b>
              <p className="text-[0.82rem] text-muted">{desc}</p>
            </div>
          </div>
        ))}
      </div>
      <p className="font-mono text-[0.85rem] text-muted">{t.arch.loopNote}</p>

      <div className="grid gap-6 min-[861px]:grid-cols-2">
        <div className="grid content-start gap-3">
          <h3 className="text-[1.05rem] font-semibold">{t.arch.guardrailsTitle}</h3>
          <ul className="m-0 grid list-none gap-3 p-0">
            {t.arch.guardrails.map(([title, body]) => (
              <li key={title} className="grid gap-0.5 border-l-2 border-line pl-3">
                <b className="text-[0.95rem]">{title}</b>
                <span className="text-[0.88rem] text-muted">{body}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="grid content-start gap-3">
          <h3 className="text-[1.05rem] font-semibold">{t.arch.ciTitle}</h3>
          <Pane title=".github/workflows/e2e.yml" badge={<Prov real />}>
            <CodeBlock text={WORKFLOW} />
          </Pane>
          <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[0.88rem]">
            {t.arch.exits.map(([code, body]) => (
              <div key={code} className="contents">
                <code className="font-mono font-semibold">{code}</code>
                <span>{body}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
