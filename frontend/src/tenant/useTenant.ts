import { useContext } from "react";
import { TenantContext } from "./TenantProvider";

export const useTenant = () => useContext(TenantContext);
