/** A step whose page is not built yet in this stage of the plan. */
export function Placeholder({ title }: { title: string }) {
  return (
    <section className="page">
      <main className="card" style={{ flex: 1 }}>
        <div className="card-head">
          <h1>{title}</h1>
        </div>
        <div className="placeholder">This page is not available in this version of the interface yet.</div>
      </main>
    </section>
  );
}
