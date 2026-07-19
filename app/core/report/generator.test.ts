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
  
  test('should accurately serialize structural multiline DOM line diffs', () => {
    const result = generateCaseStudy(mockFixture, false);
    expect(result).toContain('- <div class="btn-container">');
    expect(result).toContain('-   <button id="old-login-btn">Sign In</button>');
    expect(result).toContain('+ <div class="btn-container">');
    expect(result).toContain('+   <button id="new-login-submit">Sign In</button>');
  });

  test('should dynamically scale markdown code fence backticks to prevent payload escape sequences', () => {
    // 1. Test scaling up to 4 when content contains 3 backticks
    const scaledResult = generateCaseStudy(mockFixture, false);
    expect(scaledResult).toContain('\n````typescript\n');
    expect(scaledResult).toContain('\n````\n');

    // 2. Test floor fallback: Should maintain standard 3 backticks if content has only 1 backtick
    const simpleFixture = { ...mockFixture, patch: 'const a = `test`;' };
    const floorResult = generateCaseStudy(simpleFixture, false);
    
    // Checks that the exact 3-backtick line exists (using \n to prevent substring matching bugs)
    expect(floorResult).toContain('\n```typescript\n');
    
    // Safely checks that an exact 2-backtick line does NOT exist
    expect(floorResult).not.toContain('\n``typescript\n');
  });

  test('should execute deterministic path redaction without destroying valid structural HTML nodes', () => {
    const rawText = 'Error in /var/log/app.log and D:\\Data\\config.json inside <div> node';
    const redacted = redactPaths(rawText);
    expect(redacted).not.toContain('/var/log/app.log');
    expect(redacted).not.toContain('D:\\Data\\config.json');
    expect(redacted).toContain('<div>');
  });

  test('should match static snapshot output tracking variants structural validation', () => {
    const normalOutput = generateCaseStudy(mockFixture, false);
    const anonymizedOutput = generateCaseStudy(mockFixture, true);

    expect(normalOutput).toMatchSnapshot();
    expect(anonymizedOutput).toMatchSnapshot();
  });
});