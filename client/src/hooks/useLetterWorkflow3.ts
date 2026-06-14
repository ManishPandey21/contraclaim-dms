import { useState, useEffect, useCallback } from "react";

import { api } from "@/services/api";

import { joinApiUrl } from "@/config/api";

const parseOrganizationsResponse = (payload: any): Organization[] => {

// Handle nested response structure from backend: { organizations: [...], total, page, limit }

let collection: any[] = [];

if (Array.isArray(payload)) {

collection = payload;

} else if (payload && typeof payload === "object") {

// Check for nested organizations array (from OrganizationListResponse)

if (Array.isArray(payload.organizations)) {

collection = payload.organizations;

} else if (Array.isArray(payload.data)) {

collection = payload.data;

}

}

console.log("[parseOrganizationsResponse] Parsing organizations:", {

payloadType: typeof payload,

isArray: Array.isArray(payload),

hasOrganizations: payload?.organizations ? "yes" : "no",

collectionLength: collection.length,

});

return collection

.map((org: any) => {

// Handle both _id and id fields from backend

const id =

org?.id ??

org?._id ??

(typeof org?._id !== "undefined" ? String(org._id) : "");

const name = org?.name ?? org?.title ?? "";

const description =

org?.description ?? org?.shortName ?? org?.short_name ?? undefined;

if (!id || !name) {

console.warn(

"[parseOrganizationsResponse] Skipping org with missing id or name:",

org

);

return null;

}

return { id: String(id), name: String(name), description };

})

.filter(Boolean) as Organization[];

};