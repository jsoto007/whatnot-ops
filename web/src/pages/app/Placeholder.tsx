export function Placeholder({ title, phase }: { title: string; phase: number }) {
  return (
    <section>
      <h2>{title}</h2>
      <p className="card muted">Coming in Phase {phase} of the plan.</p>
    </section>
  )
}
