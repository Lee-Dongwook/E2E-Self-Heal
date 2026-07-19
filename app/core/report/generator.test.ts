import { generateCaseStudy, redactPaths, RunSummary } from './generator';

const mockFixture: RunSummary = {
  problem: 'Failed to click the login button on /Users/dev/project/src/components/Login.tsx',
  beforeDOM: '<div class="btn-container">\n  <button id="old-login-btn">Sign In</button>\n</div>',
  afterDOM: '<div class="btn-container">\n  <button id="new-login-submit">Sign In</button>\n</div>',
  diagnosis: 'The target element ID changed from old-login-btn to new-login-submit on Windows volume C:\\BuildAgent\\work\\.',
  patch: '// Fix patch code containing backticks\nconst config = `internal_env`;\nawait page.click("#new-login-submit");',
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
    const result = generateCaseStudy(mockFixture, false);
    expect(result).toContain('````typescript');
    expect(result).toContain('````');
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