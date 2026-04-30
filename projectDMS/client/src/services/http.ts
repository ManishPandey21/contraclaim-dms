import axios from "axios";
import { API_BASE_URL } from "../config/api";

export const createHttpClient = () =>
  axios.create({
    baseURL: API_BASE_URL,
    timeout: 20000,
    withCredentials: true,
  });

export const publicApi = createHttpClient();
