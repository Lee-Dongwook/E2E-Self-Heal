// src/core/report/generator.ts

export interface RunSummary {
  problem: string;
  beforeDOM: string;
  afterDOM: string;
  diagnosis: string;
  patch: string;
  result: 'passed' | 'failed';
}

export function generateCaseStudy(summary: RunSummary, anonymize: boolean = false): string {
  const redact = (text: string) => 
    anonymize ? text.replace(/\/[a-zA-Z0-9_/.-]+/g, '/[REDACTED]') : text;

  return `# Engineering Case Study: Automated UI Repair

## The Problem
${redact(summary.problem)}

## DOM Diff
\`\`\`diff
- ${redact(summary.beforeDOM)}
+ ${redact(summary.afterDOM)}
\`\`\`

## Diagnosis
${redact(summary.diagnosis)}

## The Patch
\`\`\`typescript
${redact(summary.patch)}
\`\`\`

## Result
The test run **${summary.result.toUpperCase()}**.`;
}