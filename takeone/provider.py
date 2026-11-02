from pathlib import Path
from collections.abc import Callable

import openai
from openai import OpenAI
from pydantic import ValidationError
from dotenv import dotenv_values

from takeone_rubric_v1.muse_adapter import propose
from .config import Settings
from .contracts import SceneAnalysis, GradeProposal, CreativeReview
from .errors import Problem
from .preferences import PromptMemoryGate
from .usage import usage_details


# What each control does in the shipped v3 operator (color_processing.apply_rgb). Pinned by tests/test_tone_semantics.py:
# every control except highlight_ceiling keeps white at white; lowering white_point or raising contrast pushes upper tones UP.
CONTROL_SEMANTICS = {
    "contrast": "0.75..1.25; neutral 1.0; bounded sigmoid x^c/(x^c+(1-x)^c), no fixed-pivot subtraction. Above 1 it deepens shadows and "
                "LIFTS upper highlights (S-curve); it never darkens the brightest pixels.",
    "gamma": "0.8..1.2; neutral 1.0; encoded luminance power 1/gamma: above 1 lifts midtones, below 1 darkens. Pure white stays white.",
    "saturation": "0..1.5; neutral 1.0; Rec.709-weighted encoded RGB channel mixing",
    "brightness_gain": "0.65..1.35, neutral 1.0. Bounded display-domain brightness, not physical relighting. Maps black to black and white to white: "
                       "below 1 it darkens midtones and upper midtones only and cannot darken pure highlights (use highlight_ceiling).",
    "cooling": "-0.15..0.15, neutral 0. Positive reduces red and increases blue using explicit Rec.709 RGB gains; not calibrated Kelvin. "
               "On bright pixels the raised blue/green channel saturates, so strong cooling of a pale sky gives a flat cyan-white, not a dark blue, "
               "unless highlight_ceiling first brings the sky down.",
    "black_point": "0..0.15 input luma level. Nonzero deliberately discards lower levels; use only when the intended look permits it.",
    "white_point": "0.75..1 input luma level. Source luma AT OR ABOVE this value becomes pure white BEFORE tone and colour, so LOWERING it "
                   "BRIGHTENS and flattens highlights (sky, clouds, sand, snow, windows, signage, glare turn white). It can never darken, subdue or protect highlights. "
                   "Keep 1.0 unless deliberate highlight clipping or lift is the intent.",
    "highlight_ceiling": "0.6..1, neutral 1.0. The ONLY control that darkens highlights: a smooth output shoulder t-(1-c)*t^2 applied after "
                         "tone, so pure white becomes c, a 0.80 tone drops by about 0.64*(1-c), a 0.50 tone by 0.25*(1-c) and shadows barely move. "
                         "Use it to subdue or darken bright areas such as sky, clouds, sand, snow, windows or glare (in a localized layer when the subject must stay bright). "
                         "Bright texture is compressed (slope 2c-1 at white), never clipped; 0.6 is the strongest supported value.",
}
HIGHLIGHT_HANDLING = (
    "To darken or subdue highlights (sky, clouds, sand, snow, windows, signage, glare) use highlight_ceiling; never lower white_point or raise contrast, which brighten them. "
    "Without highlight_ceiling a 0.90 luma highlight only falls to about 0.88 with punchy contrast (1.25); with highlight_ceiling 0.6 and the same "
    "darkening controls it reaches about 0.57, a dark steel tone once cooled, while texture is compressed rather than clipped. A global shoulder "
    "also lowers upper midtones and a bright subject, so put a strong ceiling on the surroundings/sky layer of a localized plan when the subject "
    "must stay bright. If the brief needs highlights darker than highlight_ceiling 0.6 can deliver, list that in unfulfilled_requests. "
    "Highlight policy is checked before rendering on the footage's own brightest pixels, for darkening layers only (a brightening grade is never "
    "blocked here): subdue rejects any visible highlight brightening; preserve_detail/review reject brightening caused by a lowered white_point; "
    "intentional_lift/intentional_clipping are never rejected."
)


class MuseProvider:
    def __init__(self, settings: Settings, prompt_gate=None):
        self.settings = settings
        self.prompt_gate = prompt_gate

    def available(self):
        return bool(self.key())

    def key(self):
        if self.settings.api_key:
            return self.settings.api_key
        # Allow initial key setup without restarting an already running local backend.
        # Direct Settings instances used by offline tests have no key_file.
        if self.settings.key_file and self.settings.key_file.is_file():
            return str(dotenv_values(self.settings.key_file).get("MODEL_API_KEY") or "").strip()
        return ""

    def client(self):
        key = self.key()
        if not key:
            raise Problem("MUSE_NOT_CONFIGURED", "The Meta API key is not configured.",
                          "Set MODEL_API_KEY in the root .env file, then refresh the editor.", 503)
        return OpenAI(api_key=key, base_url=self.settings.api_base,
                      timeout=self.settings.model_timeout, max_retries=0)

    def analyze(self, path: Path, duration: float, remote_id: str | None,
                remember_upload: Callable[[str], None]):
        with self.client() as client:
            if not remote_id:
                with path.open("rb") as stream:
                    remote_id = self.call(lambda: client.files.create(file=stream, purpose="user_data")).id
                remember_upload(remote_id)
            result = self.call(lambda: client.responses.parse(
                model=self.settings.model,
                instructions=(
                    "Inspect this silent video for a color-grading consultation. Describe the actual scene, subjects, "
                    "visible lighting/color relationships, motion and changes over time. Return a short summary and "
                    "timestamped observations with unique IDs, start/end seconds and uncertainty. Do not infer user "
                    "preferences or permissions. Do not invent numerical exposure/color measurements. Cite only "
                    "times inside the supplied duration. Use at most eight concise observations. "
                    "Note limitations. Output the requested schema."
                ),
                input=[{"role": "user", "content": [
                    {"type": "input_text", "text": f"Inspect the entire silent clip. Duration: {duration:.3f} seconds."},
                    {"type": "input_file", "file_id": remote_id},
                ]}],
                text_format=SceneAnalysis, reasoning={"effort": "low"}, max_output_tokens=2600, store=False,
            ))
            accounting = {"usage_reported":True, **usage_details(result.usage.model_dump() if result.usage else {})}
            parsed = result.output_parsed
            if parsed is None:
                if getattr(result, "status", None) == "incomplete":
                    raise Problem("MUSE_RESPONSE_TRUNCATED", "Muse did not complete the scene analysis within its response limit.",
                                  "The prepared source is kept. Retry the step explicitly; no automatic retry was made.", 502,**accounting)
                raise Problem("MUSE_INVALID_RESPONSE", "Muse returned no usable scene analysis.",
                              "Retry analysis. The source and preview have been kept.", 502,**accounting)
            if any(o.end > duration + 0.25 for o in parsed.observations):
                raise Problem("MUSE_INVALID_EVIDENCE", "Muse returned an observation outside the clip.",
                              "Retry analysis. Invalid evidence has not been accepted.", 502,**accounting)
            return parsed, result.usage.model_dump() if result.usage else {}

    def propose(self, session):
        with self.client() as client:
            def filtered(context):
                return self.prompt_context("rubric", context, session.inputs.project_id)
            return self.call(lambda: propose(session, self.settings.model, client=client, context_filter=filtered))

    def prompt_context(self, stage, payload, project_id=None):
        return self.prompt_gate.build(stage, payload, project_id) if self.prompt_gate else PromptMemoryGate.clean(payload)

    def grade(self, brief, analysis, source, sample_stats, images, technical_context=None):
        import json
        references = ["user_request", "default_protections",
                      *(d.source_id for d in brief.directives),
                      *("constraint:" + str(i) for i in range(len(brief.explicit_constraints)))]
        context = {
            "brief": brief.model_dump(mode="json"),
            "scene_analysis": analysis.model_dump(mode="json"),
            "source": source.model_dump(mode="json"),
            "sample_statistics": sample_stats,
            "allowed_source_ids": references,
            "controls": CONTROL_SEMANTICS,
            "highlight_handling": HIGHLIGHT_HANDLING,
            "operation_order": ["luminance_levels","bounded_luminance_tone","peak_protection","OCIO_ASC_CDL_colour"],
        }
        context['execution_capabilities']=technical_context or {'operations':['global_grade'],
            'limitations':['Localized execution not verified for this request.']}
        context = self.prompt_context("colorist", context, brief.project_id)
        content = [{"type": "text", "text": json.dumps(context)}]
        for image in images:
            content += [{"type": "text", "text": f'Source sample requested at {image["time"]:.3f}s.'},
                        {"type": "image_url", "image_url": {"url": image["url"], "detail": "low"}}]
        with self.client() as client:
            result = self.call(lambda: client.beta.chat.completions.parse(
                model=self.settings.model,
                messages=[
                    {"role": "system", "content": (
                        "You are Take One's colorist/planner. Choose global or localized execution from the whole brief, "
                        "the actual source frames, cached scene observations and declared controls. Source frames "
                        "are analysis thumbnails, not the final rendering source. Statistics are sampled encoded "
                        "luma diagnostics, not scene exposure measurements. Respect explicit constraints and "
                        "default protections. Do not compound darkness when several directives describe one look. "
                        "If the source is already dark, coordinate gamma and contrast to retain subject legibility. "
                        "Do not brighten dark footage just to satisfy a histogram or black-fraction target. Intentional darkness "
                        "is legitimate. State intent/shadow/highlight policy, and set optional execution_limits only when "
                        "a declared preservation requirement justifies those bounds; these are not confidence or quality scores. "
                        "Use black_point/white_point deliberately, not as a universal normalization. black_point is an INPUT "
                        "shadow cutoff: positive values map all source luminance at or below that value to black; it does NOT "
                        "lift the output black floor or recover shadow detail. white_point is an INPUT highlight cutoff: values "
                        "below 1 can discard bright information. Prefer neutral endpoints unless this loss is explicitly intended "
                        "and compatible with the SAME execution_limits. Do not describe input cutoff clipping as lifted blacks. "
                        "Never use white_point or higher contrast to subdue, protect or darken highlights or bright areas: "
                        "source luma at or above white_point becomes pure white, so lowering it, like raising contrast, makes bright regions brighter and flatter. "
                        "highlight_ceiling is the control that darkens highlights; pair it with contrast so the S-curve does not undo it. "
                        "Set intent.highlight_policy to subdue when the brief asks for darker, subdued, controlled or less dominant highlights or bright areas, "
                        "preserve_detail when bright detail must survive, intentional_lift only when brighter highlights are wanted (high-key, punchy pop), "
                        "intentional_clipping only when clipping is acceptable. Choose it from the brief, not the footage; follow highlight_handling and report "
                        "unattainable darker-highlight requests as unfulfilled_requests. "
                        "If a declared clipping bound cannot coexist with a requested look using supported controls, request a "
                        "targeted adjustment or report the limitation; never raise preservation bounds merely to pass. Review actual previous "
                        "candidate measurements and creative feedback in execution_capabilities. Current request epoch and "
                        "current corrections override older choices. Set review_required=true for substantive creative "
                        "changes needing rendered evaluation; false for a clearly technical/no-creative-change operation. "
                        "Preserve meaningful paint/material appearance and do not invent missing detail. "
                        "Only operations listed in execution_capabilities are executable. Global contrast-only requests "
                        "Execution capabilities are current; older brief tool-limit notes may be historical, but user constraints never are. "
                        "use mode=global and local=null, with no segmentation. If subject/background need DIFFERENT "
                        "treatments or explicit subject protection, use mode=localized with a concrete SAM noun phrase, "
                        "SAM concepts (local.concept and every region concept) must be SHORT common noun phrases SAM can see: 1-3 words naming "
                        "an object or material class, optionally one colour/kind word (rocks, rock arch, sky, water, sea, trees, building, "
                        "yellow boat, person). Never put clauses, relations, positions, lighting or mood in a concept (not 'dark coastal rocks "
                        "forming natural arch', 'sky above horizon', 'water with waves'); that detail belongs in the treatment. Give "
                        "concept_alternatives: up to 3 simpler fallbacks, most specific first (concept 'rock arch' -> ['rocks','rock']). "
                        "Use selection=all_matches for materials or areas made of several pieces (rocks, trees, clouds, water, sky); "
                        "single_instance only for one countable subject that must be told apart from similar ones. "
                        "one or more necessary time intervals, and absolute foreground/background controls. Example: "
                        "dark/cool surroundings while preserving a red motorcycle needs local protection, not a global substitute. "
                        "Darkening sky while preserving buildings can target sky, with other pixels unchanged. "
                        "parameters is the base treatment OUTSIDE local intervals, not an extra compounded grade. "
                        "Use supplied shot boundaries. Do not request tracking in unaffected intervals. "
                        "Reuse an available mask concept when it identifies the same intended subject; never change the target merely to hit a cache. "
                        "single_instance requires an unambiguous ID in each shot; all_matches is for an explicitly whole concept. "
                        "For a region being changed, such as sky, set preserve_subject_color=false and keep the other layer neutral as needed. "
                        "When different areas need different treatments (for example sky, water, foliage and buildings around a subject), "
                        "add local.regions (up to 4, only when multi_region_grade is in execution_capabilities): each is a concrete SAM noun "
                        "phrase with its own treatment; all share the plan's intervals and feather. Precedence where masks overlap: the subject, "
                        "then regions in list order, then background, which covers everything else. Put the most specific region first. "
                        "Use regions instead of compromising one background treatment, and only for areas the brief treats differently; "
                        "each region costs one SAM run and must be tracked in every required frame. "
                        "preserve_subject_color=true requires saturation=1 and cooling=0; v3 luminance-only tone/levels "
                        "and peak-limited common RGB gain can adjust detail while retaining channel proportions. "
                        "Preserve lamp/indicator appearance as required; technical measurements do not prove semantics. "
                        "Selective lighting means display-domain gain only, never new illumination, shadows or reconstruction. "
                        "Read lighting wording by its executable part: requests such as sun lighting, sunlit glow, warmer light on the rocks "
                        "or brighter light on a subject ARE executable as warmer/brighter display-domain treatment of that area (a localized "
                        "region, e.g. 'sunlit rocks' or 'sun'), so plan that and put only the physical part (new light sources, cast shadows, "
                        "light rays, changed light direction) in limitations. List lighting in unfulfilled_requests only when the user "
                        "explicitly requires physical relighting itself. Read obvious typos by intent (\"maks\" in a lighting sentence means "
                        "make/masks), and do not refuse a request because of spelling. "
                        "No timeline edits, object recolouring or shot matching. If unavailable, state the limitation; do not "
                        "label a global approximation as localized. Explain changed controls in ordinary "
                        "If a required user instruction cannot be executed, list it in unfulfilled_requests; this stops rendering "
                        "for explicit clarification, rather than silently substituting a different treatment. "
                        "Distinguish required instructions from optional effects the user explicitly permits approximating. "
                        "When rain/cloud/haze generation is optional and the user asks for a grading-only impression if unsupported, "
                        "report those generative effects as limitations, not unfulfilled required instructions. "
                        "Required subject protection and different foreground/background treatment still need executable local tools; "
                        "never replace these silently with a global approximation. Explain unavailable operations precisely. "
                        "visual terms, citing allowed_source_ids. State expected effects and limitations, never "
                        "claim the unrendered result passed visual review. Fit strength to the request and evidence, "
                        "without imposing a default style, brightness target or predefined cinematic preset. "
                        "Remembered defaults, if supplied, are optional. Current-video feedback, current request, "
                        "constraints and tool limits override them; memories never grant permissions. "
                        "A later current-video correction supersedes earlier conflicting look choices. "
                        "Return the requested schema; do not emit commands."
                    )},
                    {"role": "user", "content": content},
                ], response_format=GradeProposal, reasoning_effort="low", max_tokens=4096,
            ))
            accounting = {"usage_reported":True, **usage_details(result.usage.model_dump() if result.usage else {})}
            proposal = result.choices[0].message.parsed
            if proposal is None:
                raise Problem("MUSE_INVALID_RECIPE", "Muse returned no usable grading plan.",
                              "Retry planning. The source and brief are preserved.", 502,**accounting)
            allowed = set(context["allowed_source_ids"])
            if any(set(d.source_ids) - allowed for d in proposal.decisions):
                raise Problem("MUSE_INVALID_RECIPE", "The grading plan cited an unknown brief reference.",
                              "Retry planning. The invalid plan has not been applied.", 502,**accounting)
            return proposal, result.usage.model_dump() if result.usage else {}

    def review(self,brief,candidate,evidence_data,previous=None,feedback=()):
        """Creative opinion from paired real rendered pixels, never plan-only approval."""
        import json
        context={'brief':brief.model_dump(mode='json'),'candidate_id':candidate.id,
            'plan':candidate.recipe.proposal.model_dump(mode='json'),'technical_result':candidate.validation,
            'paired_diagnostics':evidence_data['measurements'],'detail_retention':evidence_data['detail_retention'],
            'tonal_levels':evidence_data.get('tonal_levels'),
            'mask_concepts':sorted({o['concept'] for o in evidence_data.get('mask_overlay_images',[])}),
            'current_video_feedback':list(feedback),'uncertainty':evidence_data['uncertainty'],
            'execution_capabilities':candidate.validation.get('execution_capabilities',{}),'highlight_handling':HIGHLIGHT_HANDLING,'control_semantics':CONTROL_SEMANTICS,
            'allowed_candidate_ids':[candidate.id]+([previous['id']] if previous else [])}
        content=[];allowed=set()
        for role,images in [('source',evidence_data['source_images']),('candidate',evidence_data['candidate_images']),
                            ('source_region',evidence_data.get('source_region_images',[])),('candidate_region',evidence_data.get('candidate_region_images',[])),
                            ('previous',previous['images'] if previous else [])]:
            for frame in images:
                ref=f'{role}:{frame["frame_index"]}';allowed.add(ref)
                content += [{'type':'text','text':f'{ref} at {frame["time"]:.6f}s'},
                            {'type':'image_url','image_url':{'url':frame['url'],'detail':'low'}}]
        for frame in evidence_data.get('mask_overlay_images',[]):
            ref=f'mask_overlay_{frame["overlay"]}:{frame["frame_index"]}';allowed.add(ref)
            content += [{'type':'text','text':f'{ref} at {frame["time"]:.6f}s: source frame with the SAM mask for concept '
                         f'"{frame["concept"]}" tinted and outlined ({frame["coverage"]:.1%} of the frame)'},
                        {'type':'image_url','image_url':{'url':frame['url'],'detail':'low'}}]
        context['allowed_evidence_ids']=sorted(allowed)
        context=self.prompt_context('critic',context,brief.project_id)
        content.insert(0,{'type':'text','text':json.dumps(context)})
        with self.client() as client:
            result=self.call(lambda:client.beta.chat.completions.parse(model=self.settings.model,
                messages=[{'role':'system','content':
                    "You are Take One's creative reviewer. Inspect the ACTUAL rendered/source paired images, measurements, "
                    "current brief, user corrections and any previous candidate images. Your conclusions are model opinions, "
                    "not objective quality scores or user approval. Cite only supplied visual evidence IDs for each issue. "
                    "Never diagnose a whole-video event from sparse frames; report uncertainty/not_evaluable when needed. "
                    "Distinguish intentional darkness from unintended loss of visible source detail. Respect the user's look, "
                    "not your preferred cinematic style. Current explicit request/feedback and preservation constraints "
                    "override previous choices and remembered defaults. Do not rewrite the brief or grant permissions. "
                    "Identify specific mismatches, whether supported tools can correct them, and an optional targeted "
                    "revision_plan using the existing typed controls/capabilities. Keep unaffected controls, subject and intervals unchanged. "
                    "Keep existing execution_limits unchanged; do not remove or raise a preservation bound to make a revision pass. "
                    "Positive black_point is an INPUT shadow cutoff, not an output black-floor lift. Lowering white_point maps "
                    "source highlights at or above it to pure white: it BRIGHTENS and flattens highlights and can never subdue a bright area, "
                    "so never propose it, or higher contrast, to darken highlights or bright areas; lower highlight_ceiling instead. If highlights are still too "
                    "bright with highlight_ceiling already at 0.6 (see highlight_handling), mark that issue correctable=false instead of escalating the same controls. "
                    "Both input cutoffs can discard source information; use only when compatible with the same bounds. "
                    "Use execution_capabilities as the authoritative tool envelope. Region views are crops of the same "
                    "paired real frames, not independent observations or ground-truth masks. "
                    "A revise decision needs correctable evidence-supported issues. Avoid repeating "
                    "unchanged plans. Prefer the supplied previous candidate ID if the current result worsens it; do not "
                    "automatically approve anything. Sufficient means ready for human review, never professionally verified. "
                    "Return no commands, filters, new content, physical relighting or unsupported transforms. "
                    "For every issue set target to the whole-frame direction it needs (darker_highlights, cooler, deeper_shadows, "
                    "more_contrast, ...) or null when it is not a whole-frame tonal/colour direction (subject separation, edges, a single "
                    "object). tonal_levels gives source, candidate and previous p05/median/p95, median RGB, chroma and the brightest "
                    "region's luma/RGB; check them before claiming a direction improved, and do not keep requesting a direction the "
                    "previous revision failed to move. The system measures each targeted issue on the next render and stops "
                    "revisions that do not visibly move it. For each mask concept, inspect its mask_overlay images and add one "
                    "mask_assessments entry: matches only if the tint covers the intended object and little else; partial if it "
                    "misses parts or spills onto other things; wrong_object if it marks something else; not_evaluable if unclear."},
                    {'role':'user','content':content}],response_format=CreativeReview,reasoning_effort='low',max_tokens=4096))
            review=result.choices[0].message.parsed
            if review is None:raise Problem('MUSE_INVALID_REVIEW','Muse returned no usable creative review.','The rendered candidate remains available for manual review.',502)
            if (any(set(i.evidence_ids)-allowed for i in review.issues) or any(set(m.evidence_ids)-allowed for m in review.mask_assessments)
                or review.preferred_candidate_id not in {None,*context['allowed_candidate_ids']}):
                raise Problem('MUSE_INVALID_REVIEW','The review cited evidence or a candidate it was not shown.','Rendered evidence is retained; review manually or retry explicitly.',502)
            return review,result.usage.model_dump() if result.usage else {}

    @staticmethod
    def call(operation):
        try:
            return operation()
        except openai.LengthFinishReasonError as error:
            usage = getattr(getattr(error, "completion", None), "usage", None)
            raise Problem("MUSE_RESPONSE_TRUNCATED", "Muse reached its response limit before completing the rubric.",
                          "Cached scene analysis and answers are kept. Retry this step; the video will not be resent.", 502,
                          finish_reason="length", usage_reported=True,
                          **usage_details(usage.model_dump() if usage else {}))
        except openai.ContentFilterFinishReasonError:
            raise Problem("MUSE_RESPONSE_UNAVAILABLE", "The provider did not return a usable response for this request.",
                          "Review the request or try different footage. No incomplete response has been accepted.", 502)
        except openai.AuthenticationError:
            raise Problem("MUSE_AUTH_FAILED", "Meta rejected the API credentials.",
                          "Check MODEL_API_KEY and restart the backend.", 502)
        except openai.RateLimitError:
            raise Problem("MUSE_RATE_LIMIT", "The Meta API rate or account limit was reached.",
                          "Check account usage, then retry this step when available.", 429)
        except openai.APITimeoutError:
            raise Problem("MUSE_TIMEOUT", "The Meta request timed out.",
                          "Retry explicitly if desired. The remote request may already have incurred usage.", 504)
        except openai.APIConnectionError:
            raise Problem("MUSE_CONNECTION_FAILED", "The backend could not reach Meta.",
                          "Check network access and retry the failed step.", 502)
        except openai.APIStatusError as error:
            raise Problem("MUSE_API_FAILED", "The Meta API rejected the request.",
                          "Inspect the provider status and request ID. No automatic retry was made.", 502,
                          provider_status=error.status_code, provider_request_id=getattr(error, "request_id", None))
        except (ValidationError, ValueError):
            raise Problem("MUSE_INVALID_RESPONSE", "The model response did not match the required contract.",
                          "Retry the step. No invalid response has been accepted.", 502)
