"""비공개 미리보기 도구가 공유하는 경로 정책과 안전한 진단."""
from pathlib import Path


class ReviewInputError(ValueError):
    """외부 입력 내용을 포함하지 않는 고정 진단만 사용한다."""


def validate_preview_paths(target, inputs, *, name, repo, site, private):
    target = Path(target).resolve()
    inputs = tuple(Path(path).resolve() for path in inputs)
    roots = {Path(repo).resolve()}
    # 다른 clone 및 .git 파일을 사용하는 worktree도 검사한다.
    for path in (target, *inputs):
        roots.update(parent for parent in path.parents if (parent / '.git').exists())
    sites = {Path(site).resolve(), *(root / 'site' for root in roots)}
    if (target.name != name or target in inputs
            or any(path.is_relative_to(s) for s in sites for path in (target, *inputs))
            or any(target.is_relative_to(path.parent) for path in inputs)):
        raise ReviewInputError('미리보기는 입력과 분리된 비공개 폴더에 저장해야 합니다')
    for root in roots:
        allowed = Path(private).resolve() if root == Path(repo).resolve() else root / 'pipeline/preview-data'
        if target.is_relative_to(root) and not target.is_relative_to(allowed):
            raise ReviewInputError('저장소 내부 미리보기는 pipeline/preview-data 아래에 저장해야 합니다')
    return target, inputs
