export type AuthUser = {
  id: number | string;
  username: string;
};

export type AuthResponse = {
  token: string;
  token_type: "bearer";
  expires_in: number;
  user: AuthUser;
};