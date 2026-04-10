/**
 * Apex React entry point.
 * Mounts ApexWidget into #apex-root injected by the apex_widget partial.
 */
import React from "react";
import { createRoot } from "react-dom/client";
import { ApexWidget } from "../components/apex/ApexWidget";

const el = document.getElementById("apex-root");
if (el) {
  createRoot(el).render(<ApexWidget />);
}
