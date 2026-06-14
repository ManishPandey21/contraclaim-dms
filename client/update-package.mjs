import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Read the current package.json
const mainPackagePath = path.join(__dirname, "package.json");
const testingPackagePath = path.join(__dirname, "package.testing.json");

const mainPackage = JSON.parse(fs.readFileSync(mainPackagePath, "utf8"));
const testingPackage = JSON.parse(fs.readFileSync(testingPackagePath, "utf8"));

// Merge devDependencies
mainPackage.devDependencies = {
  ...mainPackage.devDependencies,
  ...testingPackage.devDependencies,
};

// Merge scripts
mainPackage.scripts = {
  ...mainPackage.scripts,
  ...testingPackage.scripts,
};

// Write the updated package.json
fs.writeFileSync(mainPackagePath, JSON.stringify(mainPackage, null, 2));

// Clean up the temporary testing package.json
fs.unlinkSync(testingPackagePath);

console.log("Successfully updated package.json with testing dependencies");
