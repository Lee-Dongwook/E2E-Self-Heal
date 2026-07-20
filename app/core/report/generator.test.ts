import { generateCaseStudy, redactPaths, RunSummary } from './generator';

const mockFixture: RunSummary = {
  problem: 'Failed to click the login button on /Users/dev/project/src/components/Login.tsx',
  beforeDOM: '<div class="btn-container">\n  <button id="old-login-btn">Sign In</button>\n</div>',
  afterDOM: '<div class="btn-container">\n  <button id="new-login-submit">Sign In</button>\n</div>',
  diagnosis: 'The target element ID changed from old-login-btn to new-login-submit on Windows volume C:\\BuildAgent\\work\\.',
  patch: '// Fix patch code containing backticks\nconst config = ```internal_env```;\nawait page.click("#new-login-submit");',
  result: 'passed'
};

describe('Report Generator Technical Specifications', () => {
  
  test('should generate the correct full markdown report structure', () => {
    const result = generateCaseStudy(mockFixture, false);
    
    // Explicitly check for key sections instead of using a snapshot file
    expect(result).toContain('# Engineering Case Study: Automated UI Repair');
    expect(result).toContain('## The Problem');
    expect(result).toContain('## DOM Diff');
    expect(result).toContain('## Diagnosis');
    expect(result).toContain('## The Patch');
    expect(result).toContain('## Result');
    expect(result).toContain('The test run **PASSED**.');
  });

  test('should dynamically scale markdown code fence backticks', () => {
    const scaledResult = generateCaseStudy(mockFixture, false);
    expect(scaledResult).toContain('\n````typescript\n');
  });

  test('should redact paths correctly', () => {
    const rawText = 'Error in /var/log/app.log and D:\\Data\\config.json inside <div> node';
    const redacted = redactPaths(rawText);
    expect(redacted).not.toContain('/var/log/app.log');
    expect(redacted).toContain('[REDACTED_PATH]');
    expect(redacted).toContain('<div>');
  });
});