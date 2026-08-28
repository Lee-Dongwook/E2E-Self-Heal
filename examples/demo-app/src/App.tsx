import { useState, type FormEvent } from "react";

import { SubmitButton } from "./components/SubmitButton";
import { CTAButton } from "./components/CTAButton";

export function App() {
  const [submitted, setSubmitted] = useState(false);
  const [started, setStarted] = useState(false);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitted(true);
  }

  return (
    <main>
      <h1>Sign up</h1>
      <form onSubmit={handleSubmit}>
        <label>
          Name
          <input name="name" defaultValue="Ada" />
        </label>
        <SubmitButton />
      </form>
      {submitted && <p id="result">Thanks!</p>}

      <section aria-labelledby="cta-heading">
        <h2 id="cta-heading">Get started</h2>
        <CTAButton onClick={() => setStarted(true)} />
        {started && <p>Welcome!</p>}
      </section>
    </main>
  );
}
