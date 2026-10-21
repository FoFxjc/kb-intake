/** Text with the search hits between « and » (docs/api-v1.md, `match.snippet`) shown as <mark>. */
export function Snippet({ text }: { text: string }) {
  const parts: { hit: boolean; s: string }[] = [];
  let rest = text;
  while (rest) {
    const a = rest.indexOf('«');
    if (a < 0) break;
    const b = rest.indexOf('»', a + 1);
    if (b < 0) break;
    if (a > 0) parts.push({ hit: false, s: rest.slice(0, a) });
    parts.push({ hit: true, s: rest.slice(a + 1, b) });
    rest = rest.slice(b + 1);
  }
  if (rest) parts.push({ hit: false, s: rest });
  return (
    <>
      {parts.map((p, i) => (p.hit ? <mark key={i}>{p.s}</mark> : <span key={i}>{p.s}</span>))}
    </>
  );
}
