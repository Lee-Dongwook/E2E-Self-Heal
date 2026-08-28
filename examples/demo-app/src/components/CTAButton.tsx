type CTAButtonProps = {
  onClick: () => void;
};

// The className was renamed from "cta-button" to "cta-primary", breaking the scenario.
export function CTAButton({ onClick }: CTAButtonProps) {
  return (
    <button className="cta-primary" onClick={onClick} type="button">
      Get started
    </button>
  );
}
