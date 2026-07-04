// The `id` was renamed from "submit-btn" to "submit", breaking example.spec.ts.
export function SubmitButton() {
  return (
    <button id="submit" className="btn">
      Submit
    </button>
  );
}
