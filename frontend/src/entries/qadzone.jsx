/**
 * QAD-Zone React entry point.
 * Mounts the QadZone component into #qadzone-root injected by the Jinja template.
 */
import React from "react";
import { createRoot } from "react-dom/client";
import { QadZone } from "../components/qadzone/QadZone";

const el = document.getElementById("qadzone-root");
if (el) {
  createRoot(el).render(<QadZone />);
}
