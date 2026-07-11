// The `className` was renamed from "cta-button" to "cta-primary", breaking classname.spec.ts.
export function CTAButton() {
  return (
    <button className="cta-primary">
      Get started
    </button>
  );
}
