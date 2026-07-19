export interface RunSummary {
  problem: string;
  beforeDOM: string;
  afterDOM: string;
  diagnosis: string;
  patch: string;
  result: 'passed' | 'failed';
}

/**
 * Redacts absolute paths (Unix/macOS & Windows) without breaking HTML closing tags.
 */
export function redactPaths(text: string): string {
  const winPathRegex = /(?:[a-zA-Z]:\\|[\\\/])[^\s\r\n\t<>:]*(?:\\|\/)[^\s\r\n\t<>:]*/g;
  const unixPathRegex = /(?<!<)\/[a-zA-Z0-9_\.\-]+(?:\/[a-zA-Z0-9_\.\-\s]+)+/g;

  let processed = text.replace(winPathRegex, '[REDACTED_PATH]');
  return processed.replace(unixPathRegex, '[REDACTED_PATH]');
}

/**
 * Prefixes every single line of a multiline string for valid Markdown diff syntax.
 */
function prefixLines(text: string, prefix: string): string {
  return text
    .split(/\r?\n/)
    .map(line => `${prefix} ${line}`)
    .join('\n');
}

/**
 * Determines a safe backtick code fence length that won't conflict with content.
 */
function getDynamicFence(content: string): string {
  const matches = content.match(/`+/g);
  if (!matches) return '```';
  const maxLength = Math.max(...matches.map(m => m.length));
  return '`'.repeat(maxLength + 1);
}

export function generateCaseStudy(summary: RunSummary, anonymize: boolean = false): string {
  const processText = (text: string) => (anonymize ? redactPaths(text) : text);

  const cleanBefore = processText(summary.beforeDOM);
  const cleanAfter = processText(summary.afterDOM);
  const cleanPatch = processText(summary.patch);

  const diffBefore = prefixLines(cleanBefore, '-');
  const diffAfter = prefixLines(cleanAfter, '+');
  
  const patchFence = getDynamicFence(cleanPatch);

  return `# Engineering Case Study: Automated UI Repair

## The Problem
${processText(summary.problem)}

## DOM Diff
\`\`\`diff
${diffBefore}
${diffAfter}
\`\`\`

## Diagnosis
${processText(summary.diagnosis)}

## The Patch
${patchFence}typescript
${cleanPatch}${patchFence}

## Result
The test run **${summary.result.toUpperCase()}**.`;
}