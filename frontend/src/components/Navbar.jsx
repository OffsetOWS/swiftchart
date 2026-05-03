import { Menu, Send, X } from "lucide-react";
import { useState } from "react";

const links = [
  ["Features", "#features"],
  ["How it works", "#how-it-works"],
  ["Strategy", "#strategy"],
  ["Pricing", "#pricing"],
  ["FAQ", "#faq"],
];

export default function Navbar() {
  const [open, setOpen] = useState(false);

  return (
    <header className="navbar">
      <a className="logo" href="#top" aria-label="SwiftChart home">
        <span className="logo-mark">S</span>
        <span>SwiftChart</span>
      </a>

      <nav className={`nav-links ${open ? "open" : ""}`} aria-label="Main navigation">
        {links.map(([label, href]) => (
          <a key={href} href={href} onClick={() => setOpen(false)}>{label}</a>
        ))}
      </nav>

      <a className="nav-cta" href="#telegram">
        <Send size={16} /> Start on Telegram
      </a>

      <button className="menu-button" onClick={() => setOpen((value) => !value)} aria-label="Toggle menu">
        {open ? <X size={22} /> : <Menu size={22} />}
      </button>
    </header>
  );
}
