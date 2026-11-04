// The school year is stored as a stable key ("1".."4", "5+", "graduated") and only translated for display,
// so the same year is never saved as both "Năm 2" and "Year 2".
export const SCHOOL_YEARS = ["1", "2", "3", "4", "5+", "graduated"];

export function schoolYearLabel(value, language) {
  if (!value) return "";
  const vi = language === "vi";
  if (value === "graduated") return vi ? "Đã tốt nghiệp" : "Graduated";
  if (SCHOOL_YEARS.includes(value)) return vi ? `Năm ${value}` : `Year ${value}`;
  return value; // profiles saved before the keys existed keep their original label
}
