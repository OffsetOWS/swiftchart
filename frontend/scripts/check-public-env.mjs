import { loadEnv } from "vite";
import { validatePublicBuildEnv } from "../src/lib/publicBuildEnv.js";
const env = { ...loadEnv("production", process.cwd(), "VITE_"), ...process.env };
validatePublicBuildEnv(env);
console.log("Public build environment passed.");
