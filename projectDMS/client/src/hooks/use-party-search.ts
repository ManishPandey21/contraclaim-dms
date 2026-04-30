import { useState, useEffect, useMemo } from "react";
import { Party } from "../types/api";

export const usePartySearch = (parties: Party[], searchTerm: string) => {
  const [filteredParties, setFilteredParties] = useState<Party[]>(parties);

  const searchParties = useMemo(() => {
    return (parties: Party[], term: string) => {
      if (!term.trim()) {
        return parties;
      }

      const searchLower = term.toLowerCase();
      return parties.filter((party) => {
        return (
          party.name.toLowerCase().includes(searchLower) ||
          party.contactEmail?.toLowerCase().includes(searchLower) ||
          party.contactPhone?.includes(searchLower) ||
          party.representatives.some(
            (rep) =>
              rep.name.toLowerCase().includes(searchLower) ||
              rep.email.toLowerCase().includes(searchLower)
          )
        );
      });
    };
  }, []);

  useEffect(() => {
    const results = searchParties(parties, searchTerm);
    setFilteredParties(results);
  }, [parties, searchTerm, searchParties]);

  return {
    filteredParties,
    hasResults: filteredParties.length > 0,
    totalResults: filteredParties.length,
  };
};

// Helper hook for party type filtering
export const usePartyTypeFilter = (
  parties: Party[],
  type?: "Organization" | "Individual"
) => {
  const filteredParties = useMemo(() => {
    if (!type) return parties;
    return parties.filter((party) => party.type === type);
  }, [parties, type]);

  return {
    filteredParties,
    organizationCount: parties.filter((p) => p.type === "Organization").length,
    individualCount: parties.filter((p) => p.type === "Individual").length,
  };
};

// Helper hook for party sorting
export const usePartySorting = (parties: Party[]) => {
  const [sortField, setSortField] = useState<keyof Party>("name");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("asc");

  const sortedParties = useMemo(() => {
    return [...parties].sort((a, b) => {
      let comparison = 0;

      switch (sortField) {
        case "name":
          comparison = a.name.localeCompare(b.name);
          break;
        case "type":
          comparison = a.type.localeCompare(b.type);
          break;
        case "createdAt":
          comparison =
            new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime();
          break;
        default:
          comparison = 0;
      }

      return sortDirection === "asc" ? comparison : -comparison;
    });
  }, [parties, sortField, sortDirection]);

  const toggleSort = (field: keyof Party) => {
    if (field === sortField) {
      setSortDirection((prev) => (prev === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDirection("asc");
    }
  };

  return {
    sortedParties,
    sortField,
    sortDirection,
    toggleSort,
  };
};
