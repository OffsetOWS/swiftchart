import { MDXProvider } from "@mdx-js/react";
import { Menu, Search, X } from "lucide-react";
import { useMemo, useState } from "react";
import swiftChartLogo from "../assets/swiftchart-logo.png";
import { docSections, docs, getDocBySlug } from "../docs/registry.js";

function slugify(value) {
  return String(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "");
}

function pathSlug() {
  const [, root, slug] = window.location.pathname.split("/");
  if (root !== "docs") return docs[0].slug;
  return slug || docs[0].slug;
}

export default function Docs() {
  const [activeSlug, setActiveSlug] = useState(pathSlug());
  const [query, setQuery] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const activeDoc = getDocBySlug(activeSlug);
  const ActiveContent = activeDoc.Component;
  const filteredDocs = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return docs.slice(0, 6);
    return docs
      .filter((doc) => `${doc.title} ${doc.description} ${doc.section}`.toLowerCase().includes(normalized))
      .slice(0, 8);
  }, [query]);

  function openDoc(event, slug) {
    event.preventDefault();
    window.history.pushState({}, "", slug === docs[0].slug ? "/docs" : `/docs/${slug}`);
    setActiveSlug(slug);
    setSidebarOpen(false);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  const mdxComponents = {
    h1: (props) => <h1 className="docs-title" {...props} />,
    h2: ({ children, ...props }) => (
      <h2 id={slugify(children)} {...props}>
        {children}
      </h2>
    ),
    p: (props) => <p className="docs-paragraph" {...props} />,
    ul: (props) => <ul className="docs-list" {...props} />,
    li: (props) => <li {...props} />,
  };

  return (
    <main className="docs-shell">
      <header className="docs-topbar">
        <a className="docs-brand" href="/" aria-label="SwiftChart home">
          <img src={swiftChartLogo} alt="" />
          <span>Docs</span>
        </a>
        <div className="docs-search">
          <Search size={17} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search docs..." />
          {query ? (
            <div className="docs-search-results">
              {filteredDocs.length ? (
                filteredDocs.map((doc) => (
                  <a key={doc.slug} href={`/docs/${doc.slug}`} onClick={(event) => openDoc(event, doc.slug)}>
                    <span>{doc.section}</span>
                    <b>{doc.title}</b>
                  </a>
                ))
              ) : (
                <p>No docs found.</p>
              )}
            </div>
          ) : null}
        </div>
        <a className="docs-launch" href="/launch">Launch App</a>
        <button className="docs-menu-button" type="button" onClick={() => setSidebarOpen(true)} aria-label="Open docs menu">
          <Menu size={28} />
        </button>
      </header>

      <div className="docs-layout">
        <aside className={sidebarOpen ? "docs-sidebar open" : "docs-sidebar"}>
          <div className="docs-sidebar-head">
            <span>SwiftChart Docs</span>
            <button type="button" onClick={() => setSidebarOpen(false)} aria-label="Close docs menu">
              <X size={28} />
            </button>
          </div>
          {docSections.map((section) => (
            <nav key={section.label} aria-label={section.label}>
              <h2>{section.label}</h2>
              {section.pages.map((doc) => (
                <a key={doc.slug} className={doc.slug === activeDoc.slug ? "active" : ""} href={`/docs/${doc.slug}`} onClick={(event) => openDoc(event, doc.slug)}>
                  {doc.title}
                </a>
              ))}
            </nav>
          ))}
        </aside>

        <article className="docs-main">
          <div className="docs-breadcrumbs">
            <a href="/">SwiftChart</a>
            <span>/</span>
            <a href="/docs" onClick={(event) => openDoc(event, docs[0].slug)}>Docs</a>
            <span>/</span>
            <b>{activeDoc.title}</b>
          </div>
          <div className="docs-meta">
            <span>{activeDoc.section}</span>
            <p>{activeDoc.description}</p>
          </div>
          <MDXProvider components={mdxComponents}>
            <ActiveContent />
          </MDXProvider>
        </article>

        <aside className="docs-toc" aria-label="On this page">
          <h2>On this page</h2>
          {activeDoc.headings.map((heading) => (
            <a key={heading} href={`#${slugify(heading)}`}>
              {heading}
            </a>
          ))}
        </aside>
      </div>
    </main>
  );
}
