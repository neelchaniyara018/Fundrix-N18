// Supabase Edge Function entrypoint. Deploy with:  supabase functions deploy gateway --no-verify-jwt
// (--no-verify-jwt because people call it before they have a session.)
import { handler } from "./handler.js";

// @ts-ignore Deno global
Deno.serve((req: Request) => handler(req, Deno.env.toObject(), fetch));
