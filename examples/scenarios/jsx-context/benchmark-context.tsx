// This source is a benchmark fixture: it models a JSX-heavy test module whose failure points
// at the button below. The benchmark compares its enclosing JSX context with the whole file.
const fieldDefinitions = [
  { name: "firstName", label: "First name", required: true },
  { name: "lastName", label: "Last name", required: true },
  { name: "email", label: "Work email", required: true },
  { name: "company", label: "Company", required: false },
  { name: "role", label: "Role", required: false },
  { name: "team", label: "Team", required: false },
  { name: "region", label: "Region", required: false },
  { name: "timezone", label: "Timezone", required: false },
  { name: "language", label: "Language", required: false },
  { name: "notifications", label: "Notifications", required: false },
  { name: "marketing", label: "Marketing updates", required: false },
  { name: "terms", label: "Terms accepted", required: true },
];

export function AccountFormContext() {
  return (
    <section aria-label="Account form">
      {fieldDefinitions.map((field) => (
        <label key={field.name}>
          {field.label}
          <input name={field.name} required={field.required} />
        </label>
      ))}
      <button data-testid="legacy-submit">Create account</button>
    </section>
  );
}
