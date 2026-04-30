export function sanitizeName(name: string, maxLen: number = 10): string {
  if (!name) return "untitled";
  const lower = name.toLowerCase();
  const withHyphens = lower.replace(/[^a-z0-9]/g, "-");
  const trimmed = withHyphens.replace(/^-+|-+$/g, "");
  const singleHyphen = trimmed.replace(/-+/g, "-");
  return singleHyphen.substring(0, maxLen);
}

export function generatePathStructures(
  orgName: string,
  projectName: string,
  dateStr: string
): { pathStructure: string; pathStructure1: string } {
  // Parse date - assume YYYY-MM-DD
  let dateObj: Date;
  if (dateStr) {
    dateObj = new Date(dateStr);
    if (isNaN(dateObj.getTime())) {
      dateStr = new Date().toISOString().split("T")[0]; // fallback
      dateObj = new Date(dateStr);
    }
  } else {
    dateStr = new Date().toISOString().split("T")[0];
    dateObj = new Date(dateStr);
  }
  const year = dateObj.getFullYear().toString();
  const month = (dateObj.getMonth() + 1).toString().padStart(2, "0");

  const sanitizedOrg = sanitizeName(orgName);
  const sanitizedProject = sanitizeName(projectName);

  const pathStructure = `${sanitizedOrg}/${sanitizedProject}/${year}/${month}`;
  const pathStructure1 = `${sanitizedOrg}/${sanitizedProject}`;

  return { pathStructure, pathStructure1 };
}
