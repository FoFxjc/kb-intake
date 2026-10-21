import { useState } from 'react';

// The reviewer's name for "Approved by" / "Confirmed by", remembered in this browser only (a convenience; the server
// records the name sent with each request).
const KEY = 'kb-intake.name';

function read(): string {
  try {
    return window.localStorage.getItem(KEY) ?? '';
  } catch {
    return '';
  }
}

export function useName(): [string, (v: string) => void, () => void] {
  const [name, setName] = useState(read);
  const remember = () => {
    try {
      window.localStorage.setItem(KEY, name.trim());
    } catch {
      /* storage blocked: nothing to remember */
    }
  };
  return [name, setName, remember];
}
